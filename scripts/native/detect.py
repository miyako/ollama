"""Trusted main builds reuse an existing immutable native source/recipe."""

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
        # GitHub's tag endpoint excludes drafts whose tag has not been created.
        pages = json.loads(subprocess.check_output(
            ["gh", "api", "--paginate", "--slurp",
             f"repos/{repository}/releases?per_page=100"], text=True
        ))
        matches = [release for page in pages for release in page
                   if release["tag_name"] == tag]
        if len(matches) > 1:
            raise RuntimeError(f"Multiple releases found for {tag}")
        return matches[0] if matches else None
    raise RuntimeError(f"Release lookup failed: {result.stderr}")


def main():
    info = json.loads(Path(".build/source.json").read_text())
    build = True
    if (os.environ.get("GITHUB_REF") == "refs/heads/main"
            and os.environ.get("EVENT") in {"push", "schedule", "workflow_dispatch"}):
        release = existing_release(os.environ["GITHUB_REPOSITORY"], info["release_tag"])
        if release is not None:
            if release["draft"] or release["prerelease"]:
                raise RuntimeError("Existing native release is not published stable; inspect/recover it explicitly")
            build = False
    with open(os.environ["GITHUB_OUTPUT"], "a") as out:
        print(f"build={str(build).lower()}", file=out)


if __name__ == "__main__":
    main()
