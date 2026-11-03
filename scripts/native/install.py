"""Verify and stage complete native release payloads for the 4D component."""

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import stat
import subprocess
import zipfile

from build import require_payload
from detect import existing_release
from publish import TARGETS, verify_assets, verify_uploaded_assets
from source import ROOT, SHA, TAG


SOURCE_KEYS = ("repository", "tag", "version", "sha", "published_at", "recipe",
               "recipe_sha", "recipe_dirty", "release_tag")
SELECTION_KEYS = ("repository", "tag", "version", "sha", "published_at", "recipe", "release_tag")
LAYOUTS = {
    "macos-arm64": ("macOS/arm64", "ollama"),
    "macos-x86_64": ("macOS/x86_64", "ollama-x86_64"),
    "windows-x86_64": ("Windows", "ollama.exe"),
}
MACH_O = {bytes.fromhex(value) for value in ("feedface", "cefaedfe", "feedfacf",
                                          "cffaedfe", "cafebabe", "bebafeca")}


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def locked_source():
    lock = json.loads((ROOT / "scripts/native/upstream.json").read_text())
    return {**lock, "version": lock["tag"][1:],
            "release_tag": f"native-{lock['tag']}-{lock['sha'][:12]}-r{lock['recipe']}"}


def validate_source(source, expected):
    if (source["repository"] != "ollama/ollama" or not TAG.fullmatch(source["tag"])
            or not SHA.fullmatch(source["sha"]) or not SHA.fullmatch(source["recipe_sha"])
            or source["version"] != source["tag"][1:] or source["recipe_dirty"] is not False
            or type(source["recipe"]) is not int or source["recipe"] < 1
            or source["release_tag"] != f"native-{source['tag']}-{source['sha'][:12]}-r{source['recipe']}"):
        raise ValueError("Invalid native source provenance")
    if any(source[key] != expected[key] for key in SELECTION_KEYS):
        raise ValueError("Native release does not match the selected source/recipe")


def download_release(expected, downloads):
    tag = expected["release_tag"]
    release = existing_release("miyako/ollama", tag)
    if not release or release["draft"] or release["prerelease"]:
        raise RuntimeError(f"Required published native release is unavailable: {tag}")
    names = {}
    for target in TARGETS:
        archive = f"ollama-{expected['tag']}-{expected['sha'][:12]}-{target}-unsigned.zip"
        for name in (archive, archive + ".sha256", target + "-BUILD-INFO.json"):
            names[name] = target
    if len(release["assets"]) != 9 or {asset["name"] for asset in release["assets"]} != set(names):
        raise ValueError("Published native release has an incomplete/unexpected asset set")
    downloads.mkdir(parents=True)
    subprocess.run(["gh", "release", "download", tag, "--repo", "miyako/ollama",
                    "--dir", str(downloads)], check=True)
    for name, target in names.items():
        folder = downloads / f"native-{target}"
        folder.mkdir(exist_ok=True)
        shutil.move(str(downloads / name), folder / name)
    info = json.loads((downloads / "native-macos-arm64/macos-arm64-BUILD-INFO.json").read_text())
    source = {key: info[key] for key in SOURCE_KEYS}
    validate_source(source, expected)
    (downloads / "native-source").mkdir()
    (downloads / "native-source/source.json").write_text(json.dumps(source, indent=2) + "\n")
    _, assets = verify_assets(downloads, tag)
    verify_uploaded_assets(release, assets)
    if release["target_commitish"] != source["recipe_sha"]:
        raise ValueError("Native release target differs from its original build recipe")
    return downloads


def prepare(downloads, destination, expected):
    source, assets = verify_assets(downloads, expected["release_tag"])
    validate_source(source, expected)
    destination.mkdir(parents=True)
    for target, (layout, binary) in LAYOUTS.items():
        archive = next((downloads / f"native-{target}").glob("*.zip"))
        with zipfile.ZipFile(archive) as package:
            seen = set()
            for entry in package.infolist():
                relative = PurePosixPath(entry.filename)
                if (relative.is_absolute() or ".." in relative.parts or "\\" in entry.filename
                        or not entry.filename.startswith(layout + "/")
                        or stat.S_ISLNK(entry.external_attr >> 16) or entry.filename in seen):
                    raise ValueError(f"Unsafe/unexpected native ZIP entry: {entry.filename}")
                seen.add(entry.filename)
                output = destination / entry.filename
                if entry.is_dir():
                    output.mkdir(parents=True, exist_ok=True)
                else:
                    output.parent.mkdir(parents=True, exist_ok=True)
                    with package.open(entry) as incoming, output.open("wb") as outgoing:
                        shutil.copyfileobj(incoming, outgoing)
                    output.chmod(entry.external_attr >> 16 & 0o777)
        payload = destination / layout
        if not (payload / binary).is_file() or not (payload / "LICENSE").is_file():
            raise RuntimeError(f"Incomplete native component payload: {target}")
        require_payload(payload, target == "macos-arm64", suffix=".exe" if target.startswith("windows") else "")
        if target.startswith("windows") and not list(payload.glob("compiler-licenses/**/COPYING.RUNTIME")):
            raise RuntimeError("Missing Windows compiler runtime license")
    files = {}
    for path in sorted(destination.rglob("*")):
        if path.is_file():
            with path.open("rb") as stream:
                mach_o = stream.read(4) in MACH_O
            files[path.relative_to(destination).as_posix()] = {"sha256": digest(path), "mach_o": mach_o}
    manifest = {"source": source, "assets": {path.name: digest(path) for path in assets}, "files": files}
    (destination / "native-source.json").write_text(json.dumps(manifest, indent=2) + "\n")
    verify_installed(destination)
    return manifest


def verify_installed(destination, signed=False):
    manifest = json.loads((destination / "native-source.json").read_text())
    validate_source(manifest["source"], manifest["source"])
    actual = {path.relative_to(destination).as_posix()
              for path in destination.rglob("*") if path.is_file()}
    expected = set(manifest["files"]) | {"native-source.json"}
    if actual != expected:
        raise ValueError("Component native resources contain missing/extra files (including legacy binaries): "
                         f"missing={sorted(expected - actual)}, extra={sorted(actual - expected)}")
    for name, info in manifest["files"].items():
        path = destination / name
        if path.is_symlink() or ".." in PurePosixPath(name).parts or PurePosixPath(name).is_absolute():
            raise ValueError(f"Unsafe installed native path: {name}")
        if signed and info["mach_o"]:
            subprocess.run(["codesign", "--verify", "--strict", str(path)], check=True)
            architecture = "arm64" if name.startswith("macOS/arm64/") else "x86_64"
            subprocess.run(["lipo", str(path), "-verify_arch", architecture], check=True)
        elif digest(path) != info["sha256"]:
            raise ValueError(f"Component native resource checksum mismatch: {name}")
    for target, (layout, binary) in LAYOUTS.items():
        if not (destination / layout / binary).is_file():
            raise ValueError(f"Missing component executable: {target}")
    return manifest


def install(prepared, destination):
    verify_installed(prepared)
    destination.mkdir(parents=True, exist_ok=True)
    for platform in ("macOS", "Windows"):
        target = destination / platform
        if target.is_symlink():
            raise ValueError(f"Refusing a symlink component platform directory: {target}")
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(prepared / platform, target)
    shutil.copy2(prepared / "native-source.json", destination / "native-source.json")
    verify_installed(destination)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    preparing = commands.add_parser("prepare")
    preparing.add_argument("--artifacts", type=Path)
    preparing.add_argument("--source", type=Path,
                           help="Resolved source JSON (default: checked-in upstream lock)")
    preparing.add_argument("--release-tag", default="")
    preparing.add_argument("--downloads", type=Path, default=Path(".build/native-release"))
    preparing.add_argument("--destination", type=Path, required=True)
    installing = commands.add_parser("install")
    installing.add_argument("prepared", type=Path)
    installing.add_argument("destination", type=Path)
    installing.add_argument("--source", type=Path,
                            help="Resolved source JSON used during prepare (default: checked-in upstream lock)")
    verifying = commands.add_parser("verify")
    verifying.add_argument("destination", type=Path)
    verifying.add_argument("--signed", action="store_true")
    args = parser.parse_args()
    if args.command == "prepare":
        expected = json.loads(args.source.read_text()) if args.source else locked_source()
        if args.release_tag and args.release_tag != expected["release_tag"]:
            raise ValueError("Component release input does not match the selected source lock")
        downloads = args.artifacts or download_release(expected, args.downloads)
        manifest = prepare(downloads, args.destination, expected)
        print(f"Prepared all three native platforms from {manifest['source']['release_tag']}")
    elif args.command == "install":
        expected = json.loads(args.source.read_text()) if args.source else locked_source()
        validate_source(verify_installed(args.prepared)["source"], expected)
        install(args.prepared, args.destination)
    else:
        verify_installed(args.destination, args.signed)
        print("Verified complete component Resources/bin layout")


if __name__ == "__main__":
    main()
