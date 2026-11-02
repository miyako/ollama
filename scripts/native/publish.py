"""Publish only a complete, verified, new three-platform native release."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import zipfile

from detect import existing_release


TARGETS = ["macos-arm64", "macos-x86_64", "windows-x86_64"]


def api(repository, path, *arguments):
    return json.loads(subprocess.check_output(
        ["gh", "api", f"repos/{repository}/{path}", *arguments], text=True
    ))


def verify_build_run(repository, run_id, source):
    if not re.fullmatch(r"[1-9][0-9]*", run_id):
        raise ValueError("Invalid source build run ID")
    run = api(repository, f"actions/runs/{run_id}")
    if (run["event"] != "workflow_dispatch" or run["head_branch"] != "main"
            or run["head_repository"]["full_name"] != repository
            or run["path"] != ".github/workflows/native-build.yml"
            or run["status"] != "completed" or run["conclusion"] != "failure"
            or run["head_sha"] != source["recipe_sha"]):
        raise ValueError("Resume requires the original failed main publication run")
    pages = api(repository, f"actions/runs/{run_id}/jobs?per_page=100",
                "--paginate", "--slurp")
    jobs = {job["name"]: job["conclusion"] for page in pages for job in page["jobs"]}
    required = ["source", "Verify complete release artifact contract (no publication)",
                "build (macos-15, macos-arm64)",
                "build (macos-15-intel, macos-x86_64)",
                "build (windows-2022, windows-x86_64)"]
    if (any(jobs.get(name) != "success" for name in required)
            or jobs.get("publish / publish") != "failure"):
        raise ValueError("Resume requires all original builds and verification to have passed")


def verify_draft(release, tag, source, assets):
    if (not release["draft"] or release["prerelease"] or release["tag_name"] != tag
            or release["target_commitish"] != source["recipe_sha"]):
        raise RuntimeError("Unexpected draft state, tag or target; draft left unpublished")
    uploaded = release["assets"]
    if (len(uploaded) != len(assets)
            or {asset["name"] for asset in uploaded} != {path.name for path in assets}):
        raise RuntimeError("Draft asset set mismatch; draft left unpublished")
    by_name = {asset["name"]: asset for asset in uploaded}
    for path in assets:
        asset = by_name[path.name]
        with path.open("rb") as stream:
            digest = "sha256:" + hashlib.file_digest(stream, "sha256").hexdigest()
        if (asset["state"] != "uploaded" or asset["size"] != path.stat().st_size
                or asset.get("digest") != digest):
            raise RuntimeError(f"Draft asset digest/size mismatch: {path.name}; draft left unpublished")


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
    resume_id = os.environ.get("RESUME_DRAFT_ID", "")
    build_run = os.environ.get("SOURCE_RUN_ID", "")
    if bool(resume_id) != bool(build_run):
        raise ValueError("Resume requires both the draft ID and original source run ID")
    if resume_id:
        if not re.fullmatch(r"[1-9][0-9]*", resume_id):
            raise ValueError("Invalid draft release ID")
        verify_build_run(repository, build_run, source)
    elif source["recipe_sha"] != os.environ["GITHUB_SHA"]:
        raise ValueError("Artifacts were not built by this workflow revision")
    if source["recipe_dirty"]:
        raise ValueError("Refusing to publish artifacts from an uncommitted recipe")
    if not resume_id and existing_release(repository, tag) is not None:
        raise RuntimeError(f"Refusing to overwrite an existing release: {tag}")
    # Also refuse an existing tag without a release, rather than retargeting it.
    result = subprocess.run(["gh", "api", f"repos/{repository}/git/ref/tags/{tag}"],
                            capture_output=True, text=True)
    if result.returncode == 0 or "HTTP 404" not in result.stderr:
        raise RuntimeError(f"Tag already exists or could not be checked: {tag}\n{result.stderr}")
    notes = (
        f"Native Ollama CLI/server built from upstream {source['tag']}.\n\n"
        f"Source: https://github.com/ollama/ollama/commit/{source['sha']}\n"
        f"Build recipe: {source['recipe_sha']} (r{source['recipe']})\n\n"
        "Independent, unsigned builds; not official Ollama binaries or 4D component releases. "
        "macOS arm64: Metal + MLX Metal v3; macOS Intel and Windows x64: CPU only. "
        "No models, desktop application, CUDA, ROCm or Vulkan payloads. "
        "See README for installation, limitations and licenses. "
        "Each archive includes BUILD-INFO.json and upstream/runtime licenses.\n"
    )
    if resume_id:
        release_id = resume_id
    else:
        created = api(repository, "releases", "--method", "POST",
                      "-f", f"tag_name={tag}", "-f", f"target_commitish={source['recipe_sha']}",
                      "-f", f"name=Ollama {source['tag']} native (r{source['recipe']})",
                      "-f", f"body={notes}", "-F", "draft=true", "-f", "make_latest=false")
        release_id = str(created["id"])
        subprocess.run(["gh", "release", "upload", tag, "--repo", repository,
                        *map(str, assets)], check=True)
    # Never reupload on recovery. Compare GitHub's digests with all original bytes.
    release = api(repository, f"releases/{release_id}")
    verify_draft(release, tag, source, assets)
    published = api(repository, f"releases/{release_id}", "--method", "PATCH",
                    "-F", "draft=false", "-f", "make_latest=false")
    if published["draft"] or published["tag_name"] != tag:
        raise RuntimeError("Unexpected publication response; inspect release before retrying")
    print(published["html_url"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify-only", type=Path,
                        help="Verify downloaded matrix artifacts without any GitHub write operations")
    args = parser.parse_args()
    if args.verify_only:
        info = json.loads((args.verify_only / "native-source/source.json").read_text())
        source, assets = verify_assets(args.verify_only, info["release_tag"])
        if source["recipe_dirty"]:
            raise ValueError("Artifacts were built from an uncommitted recipe")
        print(json.dumps({"release_tag": source["release_tag"],
                          "recipe_sha": source["recipe_sha"],
                          "assets": [asset.name for asset in assets]}, indent=2))
    else:
        main()
