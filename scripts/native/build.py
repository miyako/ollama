"""Build upstream's native superbuild, install the entire payload, and package it."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import tempfile
import zipfile

from smoke import smoke


ROOT = Path(__file__).resolve().parents[2]
TARGETS = {
    ("Darwin", "arm64"): ("macos-arm64", Path("macOS/arm64"), "ollama", "metal_v3"),
    ("Darwin", "x86_64"): ("macos-x86_64", Path("macOS/x86_64"), "ollama-x86_64", ""),
    ("Windows", "AMD64"): ("windows-x86_64", Path("Windows"), "ollama.exe", ""),
}
WINDOWS_SYSTEM_DLLS = set("""
advapi32.dll bcrypt.dll cfgmgr32.dll combase.dll comctl32.dll comdlg32.dll
crypt32.dll d3d12.dll dbghelp.dll dxcore.dll dxgi.dll gdi32.dll gdi32full.dll
imm32.dll iphlpapi.dll kernel32.dll mpr.dll msasn1.dll msvcrt.dll ncrypt.dll
normaliz.dll ntdll.dll ole32.dll oleaut32.dll powrprof.dll propsys.dll rpcrt4.dll
sechost.dll secur32.dll setupapi.dll shell32.dll shlwapi.dll ucrtbase.dll
user32.dll userenv.dll version.dll winhttp.dll winmm.dll ws2_32.dll
""".split())


def run(*args, cwd=None, env=None):
    subprocess.run([str(arg) for arg in args], cwd=cwd, env=env, check=True)


def require_payload(payload, mlx):
    suffix = ".exe" if os.name == "nt" else ""
    for name in ["llama-server" + suffix, "llama-quantize" + suffix,
                 "GO_LICENSE", "LLAMA_CPP_LICENSE", "LLAMA_CPP_VENDORS_LICENSE"]:
        if not (payload / "lib/ollama" / name).is_file():
            raise RuntimeError(f"Incomplete upstream install: missing {name}")
    if mlx:
        for name in ["MLX_LICENSE", "MLX_C_LICENSE", "XGRAMMAR_LICENSE", "XGRAMMAR_NOTICE"]:
            if not (payload / "lib/ollama" / name).is_file():
                raise RuntimeError(f"Incomplete MLX notices: missing {name}")
        for name in ["libmlx.dylib", "libmlxc.dylib", "libollama_xgrammar.dylib", "mlx.metallib"]:
            if not (payload / "lib/ollama/mlx_metal_v3" / name).is_file():
                raise RuntimeError(f"Incomplete MLX install: missing {name}")


def archive_tree(tree, destination):
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(tree.rglob("*")):
            if path.is_file():
                # Dereference library symlinks: extracted Windows/macOS zip payloads
                # must not rely on an unzip implementation restoring symlinks.
                archive.write(path, path.relative_to(tree))


def check_macos_libraries(payload):
    for path in payload.rglob("*"):
        if not path.is_file():
            continue
        kind = subprocess.check_output(["file", "-b", str(path)], text=True)
        if "Mach-O" not in kind:
            continue
        run("lipo", path, "-verify_arch", platform.machine())
        output = subprocess.check_output(["otool", "-L", str(path)], text=True)
        for line in output.splitlines()[1:]:
            dependency = line.strip().split(" (", 1)[0]
            if dependency.startswith(("/usr/lib/", "/System/Library/")):
                continue
            if dependency.startswith(("@loader_path/", "@rpath/")):
                relative = dependency.split("/", 1)[1]
                if (path.parent / relative).is_file():
                    continue
            raise RuntimeError(f"Unpackaged dependency in {path}: {dependency}")


def bundle_windows_imports(payload, compiler_bin, system_root):
    pending = list(payload.rglob("*.exe")) + list(payload.rglob("*.dll"))
    checked = set()
    while pending:
        binary = pending.pop()
        if binary in checked:
            continue
        checked.add(binary)
        imports = subprocess.check_output(["objdump", "-p", str(binary)], text=True)
        if "file format pei-x86-64" not in imports:
            raise RuntimeError(f"Expected a native Windows x64 PE binary: {binary}")
        for name in re.findall(r"DLL Name:\s+([\w.+-]+\.dll)", imports, re.IGNORECASE):
            dependency = binary.parent / name
            if dependency.is_file():
                pending.append(dependency)
                continue
            if name.lower().startswith(("api-ms-win-", "ext-ms-win-")):
                continue
            # Match upstream's Windows OS allowlist, not arbitrary DLLs that a
            # hosted runner happens to have installed in System32.
            if name.lower() in WINDOWS_SYSTEM_DLLS and (system_root / "System32" / name).is_file():
                continue
            # CMake installs the helper DLLs; also cover the CGO executable's
            # compiler runtime imports, which may need peers beside ollama.exe.
            origin = compiler_bin / name
            if not origin.is_file():
                raise RuntimeError(f"Unpackaged Windows dependency in {binary}: {name}")
            shutil.copy2(origin, dependency)
            pending.append(dependency)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT / ".build/upstream")
    parser.add_argument("--build", type=Path, default=ROOT / ".build/native")
    parser.add_argument("--info", type=Path, default=ROOT / ".build/source.json")
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--runtime-licenses", type=Path)
    args = parser.parse_args()
    if args.jobs < 1:
        parser.error("--jobs must be positive")
    host = (platform.system(), platform.machine())
    if host not in TARGETS:
        raise RuntimeError(f"No native build recipe for {host}; use a supported native host")
    target, layout, binary, mlx = TARGETS[host]
    source, build = args.source.resolve(), args.build.resolve()
    info = json.loads(args.info.read_text())
    actual = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=source, text=True).strip()
    if actual != info["sha"]:
        raise RuntimeError(f"Checkout {actual} does not match resolved source {info['sha']}")
    run("git", "diff", "--exit-code", "HEAD", cwd=source)
    go_target = subprocess.check_output(["go", "env", "GOOS", "GOARCH"],
                                        cwd=source, text=True).splitlines()
    expected_go = ["windows" if os.name == "nt" else "darwin",
                   "arm64" if platform.machine() == "arm64" else "amd64"]
    if go_target != expected_go:
        raise RuntimeError(f"Native Go target must be {expected_go}, got {go_target}")
    # Refuse reused package/install trees: no stale backend may leak into a release.
    tree = build / "package"
    tree.mkdir(parents=True, exist_ok=False)
    payload = tree / layout
    prefix = build / "install"
    if prefix.exists():
        raise RuntimeError(f"Use a fresh install directory: {prefix}")
    env = os.environ.copy()
    for key in ["OLLAMA_LLAMA_CPP_SOURCE", "OLLAMA_MLX_SOURCE", "OLLAMA_MLX_C_SOURCE",
                "CMAKE_GENERATOR"]:
        env.pop(key, None)
    if os.name == "nt":
        # Upstream's nested CMake projects inherit the MinGW generator too.
        env["CMAKE_GENERATOR"] = "Ninja"
    cmake = ["cmake", "-S", str(source), "-B", str(build), "-G", "Ninja",
             "-DCMAKE_BUILD_TYPE=Release", f"-DOLLAMA_VERSION={info['version']}",
             f"-DOLLAMA_MLX_BACKENDS={mlx}", "-DOLLAMA_LLAMA_BACKENDS=",
             f"-DOLLAMA_BUILD_PARALLEL={args.jobs}"]
    if platform.system() == "Darwin":
        cmake += ["-DCMAKE_OSX_DEPLOYMENT_TARGET=14.0"]
        env.update(MACOSX_DEPLOYMENT_TARGET="14.0",
                   CGO_CFLAGS="-O3 -mmacosx-version-min=14.0",
                   CGO_CXXFLAGS="-O3 -mmacosx-version-min=14.0",
                   CGO_LDFLAGS="-mmacosx-version-min=14.0")
    run(*cmake, env=env)
    run("cmake", "--build", build, "--parallel", args.jobs, env=env)
    # This target is not part of ALL upstream; collect notices before installing.
    run("cmake", "--build", build, "--target", "ollama-go-license", env=env)
    run("cmake", "--install", build, "--prefix", prefix, env=env)
    payload.mkdir(parents=True)
    shutil.copytree(prefix / "lib", payload / "lib", symlinks=False)
    original_binary = "ollama.exe" if os.name == "nt" else "ollama"
    shutil.copy2(prefix / "bin" / original_binary, payload / binary)
    shutil.copy2(source / "LICENSE", payload / "LICENSE")
    if os.name == "nt":
        if not args.runtime_licenses or not args.runtime_licenses.is_dir():
            raise RuntimeError("--runtime-licenses must point to the MSYS2 compiler license directory")
        shutil.copytree(args.runtime_licenses, payload / "compiler-licenses")
        compiler = shutil.which("gcc")
        if not compiler:
            raise RuntimeError("The UCRT64 GCC compiler must remain in PATH for the import audit")
        bundle_windows_imports(payload, Path(compiler).parent, Path(os.environ["SystemRoot"]))
    require_payload(payload, mlx)
    if platform.system() == "Darwin":
        check_macos_libraries(payload)
    dependencies = {}
    for checkout in sorted(build.rglob("*-src")):
        if (checkout / ".git").exists():
            dependencies[str(checkout.relative_to(build))] = subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=checkout, text=True).strip()
    info.update(target=target, acceleration="Metal + MLX Metal v3" if mlx else "CPU only",
                native_revisions=dependencies,
                host=platform.platform(),
                tools={tool: subprocess.check_output([tool, *flags], cwd=source,
                                                     text=True).splitlines()[0]
                       for tool, flags in [("go", ["version"]), ("cmake", ["--version"]),
                                           ("ninja", ["--version"]),
                                           ("gcc" if os.name == "nt" else "cc", ["--version"])]})
    (payload / "BUILD-INFO.json").write_text(json.dumps(info, indent=2) + "\n")
    output = ROOT / ".build/artifacts"
    output.mkdir(parents=True, exist_ok=True)
    archive = output / f"ollama-{info['tag']}-{info['sha'][:12]}-{target}-unsigned.zip"
    archive_tree(tree, archive)
    # Test the archive, not a build-tree proxy. Move it outside the checkout so
    # upstream's development-path search cannot hide an incomplete payload.
    with tempfile.TemporaryDirectory(prefix="ollama-package-") as temp:
        extracted = Path(temp)
        with zipfile.ZipFile(archive) as package:
            package.extractall(extracted)
            for entry in package.infolist():
                (extracted / entry.filename).chmod(entry.external_attr >> 16 & 0o777)
        smoke(extracted / layout / binary, info["version"], build / "smoke.log")
    with archive.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    archive.with_suffix(".zip.sha256").write_text(f"{digest}  {archive.name}\n")
    shutil.copy2(payload / "BUILD-INFO.json", output / f"{target}-BUILD-INFO.json")
    print(f"Packaged {archive} ({digest})")


if __name__ == "__main__":
    main()
