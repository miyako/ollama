"""Connect verified native releases to the ordinary 4D component publication."""

import argparse
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import struct
import subprocess
import tempfile
import zipfile

from detect import existing_release
from install import digest, verify_installed
from publish import api, verify_uploaded_assets
from smoke import smoke
from source import ROOT


REPOSITORY = "miyako/ollama"


def trusted_main():
    if (os.environ["GITHUB_REPOSITORY"] != REPOSITORY
            or os.environ["GITHUB_REF"] != "refs/heads/main"
            or os.environ["GITHUB_EVENT_NAME"] not in {"push", "schedule", "workflow_dispatch"}):
        raise RuntimeError("Component publication requires a trusted miyako/ollama main event")


def component_matches(manifest):
    latest = api(REPOSITORY, "releases/latest")
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", latest["tag_name"]):
        raise ValueError("Latest release is not a normal 4D component release")
    assets = [asset for asset in latest["assets"] if asset["name"] == "native-source.json"]
    if not assets:
        return False
    by_name = {asset["name"]: asset for asset in latest["assets"]}
    if (len(latest["assets"]) != 4
            or set(by_name) != {"ollama.zip", "ollama.dmg", "native-source.json", "SHA256SUMS"}
            or any(asset["state"] != "uploaded" or asset["size"] <= 0
                   or not re.fullmatch(r"sha256:[0-9a-f]{64}", asset.get("digest") or "")
                   for asset in by_name.values())):
        raise ValueError("Latest component has incomplete/invalid native publication assets")
    with tempfile.TemporaryDirectory(prefix="ollama-component-source-") as temp:
        subprocess.run(["gh", "release", "download", latest["tag_name"], "--repo", REPOSITORY,
                        "--pattern", "native-source.json", "--pattern", "SHA256SUMS", "--dir", temp],
                       check=True)
        path = Path(temp) / "native-source.json"
        for name in ("native-source.json", "SHA256SUMS"):
            if by_name[name].get("digest") != "sha256:" + digest(Path(temp) / name):
                raise ValueError("Latest component provenance/checksum asset mismatch")
        lines = (Path(temp) / "SHA256SUMS").read_text().splitlines()
        expected = {f"{by_name[name]['digest'].removeprefix('sha256:')}  {name}"
                    for name in ("ollama.dmg", "ollama.zip", "native-source.json")}
        if len(lines) != 3 or set(lines) != expected:
            raise ValueError("Latest component archive checksum contract mismatch")
        return json.loads(path.read_text()) == manifest


def dispatch(manifest_path):
    trusted_main()
    manifest = json.loads(manifest_path.read_text())
    if component_matches(manifest):
        print(f"SKIP: component already contains verified {manifest['source']['release_tag']}")
        return
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    if head != os.environ["GITHUB_SHA"] or api(REPOSITORY, "git/ref/heads/main")["object"]["sha"] != head:
        raise RuntimeError("main changed during native validation; rerun before component adoption")
    source = manifest["source"]
    lock_path = ROOT / "scripts/native/upstream.json"
    lock = {key: source[key] for key in ("repository", "tag", "sha", "published_at", "recipe")}
    if json.loads(lock_path.read_text()) != lock:
        lock_path.write_text(json.dumps(lock, indent=2) + "\n")
        subprocess.run(["git", "config", "user.name", "github-actions[bot]"], check=True)
        subprocess.run(["git", "config", "user.email",
                        "41898282+github-actions[bot]@users.noreply.github.com"], check=True)
        subprocess.run(["git", "add", "scripts/native/upstream.json"], check=True)
        subprocess.run(["git", "commit", "-m", f"Adopt verified Ollama {source['tag']}",
                        "-m", f"Native release: {source['release_tag']}; source: {source['sha']}",
                        "-m", "Co-authored-by: Copilot App <223556219+Copilot@users.noreply.github.com>"],
                       check=True)
        subprocess.run(["git", "push", "origin", "HEAD:refs/heads/main"], check=True)
        head = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    # Do not wait here: the component workflow uses the same release lock and
    # starts after this native workflow exits.
    subprocess.run(["gh", "workflow", "run", "publish.yml", "--repo", REPOSITORY, "--ref", "main",
                    "-f", "use_self_hosted=false", "-f", "mode=patch",
                    "-f", f"source_sha={head}", "-f", f"native_release_tag={source['release_tag']}"],
                   check=True)
    print(f"Dispatched component Publish with complete {source['release_tag']} resources")


def select_version():
    trusted_main()
    current = json.loads((ROOT / "package.json").read_text())["version"]
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", current):
        raise ValueError("Invalid current component version")
    release = existing_release(REPOSITORY, current)
    if release and (release["draft"] or release["prerelease"]):
        raise RuntimeError("Existing component draft/prerelease requires inspection, not another version bump")
    bump = release is not None
    latest = api(REPOSITORY, "releases/latest")["tag_name"]
    if (not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", latest)
            or (bump and current != latest)
            or (not bump and tuple(map(int, current.split("."))) <= tuple(map(int, latest.split("."))))):
        raise ValueError("Current component version is inconsistent with the latest component")
    with open(os.environ["GITHUB_OUTPUT"], "a") as output:
        output.write(f"bump={str(bump).lower()}\nversion={current}\n")
    print("Bump published component version" if bump else f"Reuse unpublished {current} after an earlier failed build")


def apple_double(relative, data, expected_entries):
    if not relative.name.startswith("._"):
        return False
    counterpart = (relative.parent / relative.name[2:]).as_posix()
    if counterpart not in expected_entries or len(data) < 26:
        raise ValueError(f"Unexpected component metadata sidecar: {relative}")
    magic, version, _, count = struct.unpack(">II16sH", data[:26])
    table_end = 26 + count * 12
    if magic != 0x00051607 or version != 0x00020000 or count not in (1, 2) or table_end > len(data):
        raise ValueError(f"Invalid AppleDouble header: {relative}")
    entries = [struct.unpack(">III", data[26 + index * 12:38 + index * 12])
               for index in range(count)]
    identifiers = {identifier for identifier, _, _ in entries}
    if len(identifiers) != count or 9 not in identifiers or not identifiers <= {2, 9}:
        raise ValueError(f"Unexpected AppleDouble entry types: {relative}")
    occupied = []
    for identifier, offset, length in entries:
        if offset < table_end or offset + length > len(data) or (identifier == 9 and length < 32):
            raise ValueError(f"Invalid AppleDouble entry bounds: {relative}")
        if length:
            if any(offset < end and start < offset + length for start, end in occupied):
                raise ValueError(f"Overlapping AppleDouble entries: {relative}")
            occupied.append((offset, offset + length))
    return True


def verify_archive(archive, expected_manifest, signed):
    with tempfile.TemporaryDirectory(prefix="ollama-component-zip-") as temp:
        root = Path(temp)
        with zipfile.ZipFile(archive) as package:
            manifests = [name for name in package.namelist()
                         if name.endswith("/Resources/bin/native-source.json")]
            if len(manifests) != 1:
                raise ValueError("Component ZIP must contain exactly one native resource manifest")
            prefix = manifests[0][:-len("native-source.json")]
            expected_files = set(expected_manifest["files"]) | {"native-source.json"}
            expected_entries = expected_files | {
                parent.as_posix() for name in expected_files for parent in PurePosixPath(name).parents
                if parent != PurePosixPath(".")}
            seen = set()
            for entry in package.infolist():
                if not entry.filename.startswith(prefix) or entry.is_dir():
                    continue
                relative = PurePosixPath(entry.filename[len(prefix):])
                if relative.is_absolute() or ".." in relative.parts or "\\" in str(relative):
                    raise ValueError("Unsafe component ZIP resource path")
                if relative in seen:
                    raise ValueError(f"Duplicate component ZIP resource: {relative}")
                seen.add(relative)
                data = package.read(entry)
                # ditto preserves codesigned data-file xattrs as AppleDouble
                # sidecars; these are metadata, not additional runtime files.
                if apple_double(relative, data, expected_entries):
                    continue
                output = root / str(relative)
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_bytes(data)
                output.chmod(entry.external_attr >> 16 & 0o777)
        actual = verify_installed(root, signed)
        if actual != expected_manifest:
            raise ValueError("Component ZIP native metadata differs from verified release inputs")
        if signed:
            binary = "macOS/arm64/ollama" if platform.machine() == "arm64" else "macOS/x86_64/ollama-x86_64"
            smoke(root / binary, actual["source"]["version"],
                  (ROOT / ".build/component-zip-smoke.log"))
        print("PASS: actual component ZIP has all three complete native runtime layouts")


def publish_component(version, dmg, archive, manifest_path):
    trusted_main()
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
        raise ValueError("Invalid component release version")
    manifest = json.loads(manifest_path.read_text())
    verify_archive(archive, manifest, signed=True)
    if existing_release(REPOSITORY, version):
        raise RuntimeError("Refusing to overwrite an existing component release")
    tag = subprocess.run(["gh", "api", f"repos/{REPOSITORY}/git/ref/tags/{version}"],
                         capture_output=True, text=True)
    if tag.returncode == 0 or "HTTP 404" not in tag.stderr:
        raise RuntimeError("Component tag already exists or could not be checked")
    sums = ROOT / ".build/SHA256SUMS"
    assets = [dmg, archive, manifest_path]
    sums.write_text("".join(f"{digest(path)}  {path.name}\n" for path in assets))
    assets.append(sums)
    source = manifest["source"]
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    notes = (f"4D component with complete native server/runtime payloads from {source['release_tag']}.\n\n"
             f"Upstream: {source['tag']} / {source['sha']}\n"
             f"Original native build recipe: {source['recipe_sha']}\n"
             f"Component revision: {head}\n\n"
             "Includes macOS ARM64 Metal/MLX, macOS Intel CPU and Windows x64 CPU runtimes. "
             "Native provenance/checksums are included; component macOS code is signed during packaging.")
    created = api(REPOSITORY, "releases", "--method", "POST", "-f", f"tag_name={version}",
                  "-f", f"target_commitish={head}", "-f", f"name={version}", "-f", f"body={notes}",
                  "-F", "draft=true", "-f", "make_latest=false")
    release_id = created["id"]
    subprocess.run(["gh", "release", "upload", version, "--repo", REPOSITORY,
                    *map(str, assets)], check=True)
    release = api(REPOSITORY, f"releases/{release_id}")
    if (not release["draft"] or release["prerelease"] or release["tag_name"] != version
            or release["target_commitish"] != head):
        raise RuntimeError("Unexpected component draft; inspect before retrying")
    verify_uploaded_assets(release, assets)
    published = api(REPOSITORY, f"releases/{release_id}", "--method", "PATCH",
                    "-F", "draft=false", "-f", "make_latest=true")
    if published["draft"] or published["tag_name"] != version or published["target_commitish"] != head:
        raise RuntimeError("Unexpected component publication response; inspect before retrying")
    print(published["html_url"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    adopting = commands.add_parser("dispatch")
    adopting.add_argument("manifest", type=Path)
    commands.add_parser("version")
    publishing = commands.add_parser("publish")
    publishing.add_argument("--version", required=True)
    publishing.add_argument("--dmg", type=Path, required=True)
    publishing.add_argument("--zip", type=Path, required=True)
    publishing.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "dispatch":
        dispatch(args.manifest)
    elif args.command == "version":
        select_version()
    else:
        publish_component(args.version, args.dmg, args.zip, args.manifest)


if __name__ == "__main__":
    main()
