"""Resolve published upstream releases once, before any platform builds."""

import argparse
import json
import os
from pathlib import Path
import re
import subprocess


ROOT = Path(__file__).resolve().parents[2]
TAG = re.compile(r"v[0-9]+\.[0-9]+\.[0-9]+")
SHA = re.compile(r"[0-9a-f]{40}")


def api(path):
    return json.loads(subprocess.check_output(["gh", "api", path], text=True))


def resolve(selection, tag="", expected_sha=""):
    lock = json.loads((ROOT / "scripts/native/upstream.json").read_text())
    if selection == "pinned":
        tag, expected_sha = lock["tag"], lock["sha"]
    elif selection == "latest":
        tag = api("repos/ollama/ollama/releases/latest")["tag_name"]
    if not TAG.fullmatch(tag):
        raise ValueError("Select a stable vMAJOR.MINOR.PATCH release tag, not a branch or URL")
    if expected_sha and not SHA.fullmatch(expected_sha):
        raise ValueError("expected_sha must be a full lowercase commit SHA")
    release = api(f"repos/ollama/ollama/releases/tags/{tag}")
    if release["draft"] or release["prerelease"] or release["tag_name"] != tag:
        raise ValueError("The tag must identify a published stable upstream release")
    # The commits endpoint dereferences both lightweight and annotated tags.
    sha = api(f"repos/ollama/ollama/commits/{tag}")["sha"]
    if not SHA.fullmatch(sha) or (expected_sha and sha != expected_sha):
        raise ValueError(f"Upstream revision mismatch: expected {expected_sha}, got {sha}")
    return {
        "repository": "ollama/ollama",
        "tag": tag,
        "version": tag[1:],
        "sha": sha,
        "published_at": release["published_at"],
        "recipe": lock["recipe"],
        "recipe_sha": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "recipe_dirty": bool(subprocess.check_output(
            ["git", "status", "--porcelain"], cwd=ROOT, text=True
        ).strip()),
        "release_tag": f"native-{tag}-{sha[:12]}-r{lock['recipe']}",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selection", choices=["pinned", "latest", "tag"], default="pinned")
    parser.add_argument("--tag", default="")
    parser.add_argument("--expected-sha", default="")
    parser.add_argument("--output", type=Path, default=ROOT / ".build/source.json")
    args = parser.parse_args()
    info = resolve(args.selection, args.tag, args.expected_sha)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(info, indent=2) + "\n")
    print(json.dumps(info, indent=2))
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as out:
            for key in ["tag", "sha", "version", "release_tag"]:
                print(f"{key}={info[key]}", file=out)


if __name__ == "__main__":
    main()
