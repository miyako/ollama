![version](https://img.shields.io/badge/version-20%2B-E23089)
![platform](https://img.shields.io/static/v1?label=platform&message=mac-intel%20|%20mac-arm%20|%20win-64&color=blue)
[![license](https://img.shields.io/github/license/miyako/ollama)](LICENSE)
![downloads](https://img.shields.io/github/downloads/miyako/ollama/total)

# Ollama

Run the [Ollama](https://github.com/ollama/ollama) server/CLI from 4D.
[4D usage and OpenAI-compatible API examples](https://miyako.github.io/ollama/).

This repository has **two separate products**: the existing 4D component
(`package.json`, version `20.1.5`, releases such as `20.1.5` with `ollama.zip`/`.dmg`)
and independently built native server archives (`native-v…` releases).
Native builds do not change the component version, commit binaries to the repository,
or automatically replace its legacy bundled Intel executable.

## Selected upstream source

Verified against GitHub's [latest stable release API](https://api.github.com/repos/ollama/ollama/releases/latest)
on **2026-11-02**:
**[v0.40.13](https://github.com/ollama/ollama/releases/tag/v0.40.13)**,
published 2026-10-28, commit
[`04088e7c9d41cba5d33bafbf788d87a465c79d5b`](https://github.com/ollama/ollama/commit/04088e7c9d41cba5d33bafbf788d87a465c79d5b).
[The reviewed pin](scripts/native/upstream.json) is used for push/PR builds.
It is not a claim that upstream `main` or this pin will always be the latest release.

The [upstream development guide at that revision](https://github.com/ollama/ollama/blob/04088e7c9d41cba5d33bafbf788d87a465c79d5b/docs/development.md)
and root `cmake/local.cmake` define the build. We run the **native CMake superbuild**,
not just `go build`, and never download official Ollama binaries.
CMake builds the actual CLI/server, patched pinned llama.cpp `llama-server` and
`llama-quantize`, and native runtime libraries. On Apple Silicon it also builds MLX,
MLX-C, XGrammar and Metal assets from upstream-selected sources.

| Archive target | Native CI runner | Inference payload | Minimum / limitations |
|---|---|---|---|
| `macos-arm64` | `macos-15` | llama.cpp Metal + MLX Metal v3 | macOS 14+, Apple Silicon; no Metal v4 overlay |
| `macos-x86_64` | `macos-15-intel` | CPU, dynamically loaded CPU variants | macOS 14+, Intel; no Metal or MLX/safetensor engine |
| `windows-x86_64` | `windows-2022` | CPU, dynamically loaded CPU variants | Windows 10/11 x64, UCRT; no MLX/safetensor engine |

These are **unsigned, independent builds**, not official Ollama distributions.
No GUI application, model files, CUDA, ROCm, Vulkan, Linux or Windows ARM archives
are supplied. Hardware-specific GPU variants need separate SDKs and hardware tests;
the CPU-only targets must not be advertised as GPU-accelerated.
The host build smoke does not prove model inference or older OS compatibility.

## Install / use native archives

Download a matching `ollama-vVERSION-SHA12-TARGET-unsigned.zip` and its `.zip.sha256`
from a **`native-…` [release](https://github.com/miyako/ollama/releases)**.
Do not use the GitHub-generated source ZIP as a native payload.
Verify SHA-256 (`shasum -a 256 -c FILE.zip.sha256` on macOS, or
`Get-FileHash FILE.zip -Algorithm SHA256` on Windows and compare the sidecar).
Extract the **whole archive**, preserving directories, not just the executable.

```text
macOS/arm64/ollama                      # Apple Silicon archive
macOS/arm64/lib/ollama/…                # helpers, MLX libraries/assets, licenses
macOS/x86_64/ollama-x86_64               # Intel archive
macOS/x86_64/lib/ollama/…                # Intel helpers/libraries/licenses
Windows/ollama.exe                      # Windows x64 archive
Windows/lib/ollama/…                    # helpers, backend and compiler runtime DLLs
```

Each payload also contains `LICENSE`, `BUILD-INFO.json`, and (Windows)
`compiler-licenses/`. Runtime notices include `GO_LICENSE`, llama.cpp/vendor
licenses, and MLX dependency notices where applicable. Keep them when redistributing.
This repository's MIT license does not replace third-party license terms.

For 4D, merge these platform directories into the component's
`ollama/Resources/bin/` (or its installed component `Resources/bin/`).
The updated launcher prefers the matching macOS architecture directory and retains
the old flat-directory lookup if the matching executable is absent. Both Mac archives can
coexist without mixing incompatible libraries. Windows keeps its existing executable
path. Install the updated component source as well to use the Mac subdirectories.
The standalone CLI does not depend on 4D:

```sh
chmod +x macOS/arm64/ollama macOS/arm64/lib/ollama/llama-{server,quantize}
OLLAMA_HOST=127.0.0.1:8080 macOS/arm64/ollama serve
# In another terminal:
curl --fail http://127.0.0.1:8080/api/version
```

On Intel substitute `macOS/x86_64/ollama-x86_64`. In PowerShell:
`$env:OLLAMA_HOST='127.0.0.1:8080'; .\Windows\ollama.exe serve`.
macOS executables may need execute permissions restored by the unzip tool.
There is **no Developer ID signing/notarization** in the native pipeline; Gatekeeper
or Windows policy may block downloaded executables. Follow your organization's
approval/signing process; the existing signed 4D DMG pipeline is separate.
4D itself and its component dependencies remain required for the 4D examples.

## GitHub Actions and publishing

**Build Ollama from source** (`native-build.yml`):

- PRs and relevant pushes to `main` build the reviewed pin on all three native runners.
- Weekly runs resolve the latest *published stable* upstream release, excluding drafts
  and prereleases, then resolve its tag once to a full commit SHA before the matrix.
  An already published matching `native-…-rN` release skips the scheduled build.
  Unpublished updates remain eligible for rebuilds; no scheduled run publishes.
- Manual runs use latest stable by default; `upstream_tag` selects an older stable
  release and `expected_sha` optionally guards against a moved tag. Older releases
  must support the current superbuild interface; incompatible layouts fail explicitly.
- Every run records source tag/SHA, upstream publication time and recipe commit;
  packages also record tool versions and fetched native revisions. Downloads include
  archives, per-archive SHA-256 sidecars and platform build manifests.

To publish, dispatch from **`miyako/ollama`'s `main`** with **`publish=true`**.
The reusable **Publish native Ollama release** workflow runs only after every native
build and smoke check succeeds. Only its publishing job has `contents: write`.
It verifies the complete three-target asset set, checksums and matching source/recipe
metadata, creates a **new draft**, uploads all assets and then publishes it.
Native release names are `native-vVERSION-SHA12-rN`; component tags are untouched.
Existing tags/releases are refused, assets are never clobbered, and native releases
are marked **not latest**, preserving the component's latest-release convention.
Failed uploads leave an unpublished draft for inspection, not a partially published
release. Retrying publication requires explicitly removing that failed draft/tag after
inspection or incrementing `recipe` in the reviewed pin; it is not an overwrite switch.

Use the Actions artifacts to review scheduled updates before publishing. To adopt a
new default pin, update `scripts/native/upstream.json` with the verified stable tag,
full SHA and publication date. Increment `recipe` for a new package of the same source.
Changing SDKs/toolchains may change output bytes: revision recording makes the inputs
auditable, **not bit-for-bit reproducible** across mutable hosted runner images.

## Build and validate locally

Prerequisites: Python 3.11+, Git, authenticated `gh` (for resolution), CMake 3.24+,
Ninja, and Go **1.26.10** for the selected source (`go.mod` is authoritative).
Go's automatic toolchain download must be allowed or that version preinstalled.
Use full Xcode/Clang and the Metal toolchain on macOS ARM
(`xcodebuild -downloadComponent MetalToolchain` if missing).
For this Windows CPU recipe use a native Python and MSYS2 **UCRT64** GCC, CMake and
Ninja (`mingw-w64-ucrt-x86_64-{gcc,cmake,ninja}`), put `C:\msys64\ucrt64\bin`
first in PATH, and set `CC=gcc`, `CXX=g++`. Upstream also supports MSVC;
this recipe deliberately uses its supported MinGW path so CGO and CMake share a compiler.

```sh
python3 scripts/native/source.py --selection pinned
git clone --branch v0.40.13 --depth 1 https://github.com/ollama/ollama.git .build/upstream
git -C .build/upstream checkout --detach 04088e7c9d41cba5d33bafbf788d87a465c79d5b
python3 scripts/native/build.py --jobs 4
python3 -m unittest discover -s scripts/native -p 'test_*.py' -v
actionlint .github/workflows/native-build.yml .github/workflows/native-publish.yml
```

On Windows use `python` and add
`--runtime-licenses C:/msys64/ucrt64/share/licenses` to the build command.
Use a fresh build directory for each package (`--build .build/another-build`).
For a manual/latest resolution, check out the **SHA in `.build/source.json`** rather
than the example pin above. Network access is required for Go modules and upstream's
pinned native dependency repositories. Outputs stay under ignored `.build/`.

The build installs the complete upstream runtime, explicitly collects Go licenses
(not an upstream default build target), and runs model-free checks from the final
extracted archive layout outside the checkout with SDK library paths removed:
helper execution, a fresh `serve` process,
`/api/version`, `/api/tags` with an empty isolated model store, and CLI version.
The ARM archive additionally checks MLX initialization via the internal runner with
an intentionally missing local manifest, expecting initialization followed by a
file-not-found error, without a model download.
The server binds `127.0.0.1:0`; checks use only its logged OS-assigned listener,
not a pre-existing server on 11434/8080. Only the spawned server is terminated.
No large models are downloaded. CI on Intel/Windows and a 4D integration run are
still required before treating those environments as validated locally.
Packaging also audits Mach-O architecture/library paths on macOS and recursively
checks Windows PE imports, copying compiler runtime imports and rejecting unresolved
non-system DLLs. A DLL merely existing in a hosted runner's System32 is not sufficient.

## Separate follow-up proposals

Ranked from concrete existing code evidence; these are **not implemented** here:

| Priority | Proposal | Evidence / expected benefit |
|---|---|---|
| 1 — bug | Normalize constructor options and port before worker lookup and `_main` | `ollama.4dm` sets `This.options` but passes the original nullable `$options` to `_main`; `start.4dm` dereferences `$options.options.port`. Default `.new()` can lose normalized host/options; invalid ports are also checked only after `isRunning($port)`. Add default/invalid-port 4D regression cases. |
| 2 — startup risk | Make the API-readiness contract explicit before model installation | `Methods/start.4dm` calls worker `start` then immediately `pull`; no API-ready check is visible here. Verify the external `workers.start` contract; if it only launches the process, slow startup can race installation. Add bounded `/api/version` polling with process-exit/error reporting. |
| 3 — bug | Use the standard `ollama` PATH name on Intel fallback | `_ollama.4dm` chooses `ollama-x86_64`; `_CLI.4dm` uses that same name on PATH when no bundled file exists. A normal upstream installation exposes `ollama`, not that alias. Separate bundled filename from PATH command and test both layouts. |
| 4 — feature | Signed/notarized native releases and model-backed inference tests | Native archives are unsigned and model-free health checks cannot verify tensor execution. Add gated signing and a separately budgeted small-model test per backend before adding CUDA/Vulkan/ROCm support. |
