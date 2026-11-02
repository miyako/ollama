"""Publish only a complete, verified, new three-platform native release."""

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import zipfile

from detect import existing_release


TARGETS = ["macos-arm64", "macos-x86_64", "windows-x86_64"]


def verify_assets(downloads, tag):
    source = json.loads((downloads / "native-source/source.json").read_text())
    expected_tag = f"native-{source['tag']}-{source['sha'][:12]}-r{source['recipe']}"
    if tag != expected_tag or source["release_tag"] != tag:
        raise ValueError("Release tag does not match the recorded source")
    if not re.fullmatch(r"native-v\d+\.\d+\.\d+-[0-9a-f]{12}-r\d+", tag):
        raise ValueError("Invalid native release namespace")
    assets = []
    for target in TARGETS:
        folder = downloads / f"native-{target}"
        archives = list(folder.glob("*.zip"))
        if len(archives) != 1:
            raise ValueError(f"Expected exactly one archive for {target}")
        archive = archives[0]
        expected_name = f"ollama-{source['tag']}-{source['sha'][:12]}-{target}-unsigned.zip"
        if archive.name != expected_name:
            raise ValueError(f"Unexpected archive name: {archive.name}")
        checksum = archive.with_suffix(".zip.sha256")
        with archive.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if checksum.read_text() != f"{digest}  {archive.name}\n":
            raise ValueError(f"Checksum mismatch: {archive.name}")
        metadata = folder / f"{target}-BUILD-INFO.json"
        info = json.loads(metadata.read_text())
        if any(info.get(key) != value for key, value in source.items()) or info["target"] != target:
            raise ValueError(f"Source mismatch: {target}")
        with zipfile.ZipFile(archive) as package:
            manifests = [name for name in package.namelist() if name.endswith("/BUILD-INFO.json")]
            if len(manifests) != 1 or json.loads(package.read(manifests[0])) != info:
                raise ValueError(f"Archive metadata mismatch: {target}")
        assets.extend([archive, checksum, metadata])
    return source, assets


def main():
    if (os.environ["GITHUB_REPOSITORY"] != "miyako/ollama"
            or os.environ["GITHUB_REF"] != "refs/heads/main"
            or os.environ["GITHUB_EVENT_NAME"] != "workflow_dispatch"):
        raise RuntimeError("Publication is restricted to explicit dispatches from miyako/ollama main")
    repository, tag = os.environ["GITHUB_REPOSITORY"], os.environ["RELEASE_TAG"]
    source, assets = verify_assets(Path(".build/downloads"), tag)
    if source["recipe_sha"] != os.environ["GITHUB_SHA"]:
        raise ValueError("Artifacts were not built by this workflow revision")
    if source["recipe_dirty"]:
        raise ValueError("Refusing to publish artifacts from an uncommitted recipe")
    if existing_release(repository, tag) is not None:
        raise RuntimeError(f"Refusing to overwrite an existing release: {tag}")
    # Also refuse an existing tag without a release, rather than retargeting it.
    result = subprocess.run(["gh", "api", f"repos/{repository}/git/ref/tags/{tag}"],
                            capture_output=True, text=True)
    if result.returncode == 0 or "HTTP 404" not in result.stderr:
        raise RuntimeError(f"Tag already exists or could not be checked: {tag}\n{result.stderr}")
    notes = Path(".build/release-notes.txt")
    notes.write_text(
        f"Native Ollama CLI/server built from upstream {source['tag']}.\n\n"
        f"Source: https://github.com/ollama/ollama/commit/{source['sha']}\n"
        f"Build recipe: {source['recipe_sha']} (r{source['recipe']})\n\n"
        "Independent, unsigned builds; not official Ollama binaries or 4D component releases. "
        "macOS arm64: Metal + MLX Metal v3; macOS Intel and Windows x64: CPU only. "
        "No models, desktop application, CUDA, ROCm or Vulkan payloads. "
        "See README for installation, limitations and licenses. "
        "Each archive includes BUILD-INFO.json and upstream/runtime licenses.\n"
    )
    common = ["--repo", repository]
    subprocess.run(["gh", "release", "create", tag, *common, "--draft", "--latest=false",
                    "--target", os.environ["GITHUB_SHA"], "--title", f"Ollama {source['tag']} native (r{source['recipe']})",
                    "--notes-file", str(notes), *map(str, assets)], check=True)
    # An upload failure leaves a draft for inspection; never silently publish a subset.
    release = existing_release(repository, tag)
    expected = {path.name for path in assets}
    if not release or not release["draft"] or {a["name"] for a in release["assets"]} != expected:
        raise RuntimeError("Draft asset set mismatch; draft left unpublished")
    subprocess.run(["gh", "release", "edit", tag, *common, "--draft=false", "--latest=false"],
                   check=True)


if __name__ == "__main__":
    main()
