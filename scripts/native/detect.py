"""Scheduled builds skip only a published native release of the exact recipe."""

import json
import os
from pathlib import Path
import subprocess


def existing_release(repository, tag):
    result = subprocess.run(
        ["gh", "api", f"repos/{repository}/releases/tags/{tag}"],
        text=True, capture_output=True
    )
    if result.returncode == 0:
        return json.loads(result.stdout)
    if "HTTP 404" in result.stderr:
        return None
    raise RuntimeError(f"Release lookup failed: {result.stderr}")


def main():
    info = json.loads(Path(".build/source.json").read_text())
    build = True
    if os.environ.get("EVENT") == "schedule":
        release = existing_release(os.environ["GITHUB_REPOSITORY"], info["release_tag"])
        build = release is None or release["draft"] or release["prerelease"]
    with open(os.environ["GITHUB_OUTPUT"], "a") as out:
        print(f"build={str(build).lower()}", file=out)


if __name__ == "__main__":
    main()
