---
layout: default
title: Use with 4D
description: "Install and run Ollama from 4D: native builds, installation, the 4D class and AI Kit."
guide: true
---

![version](https://img.shields.io/badge/version-20%2B-E23089)
![platform](https://img.shields.io/static/v1?label=platform&message=mac-intel%20|%20mac-arm%20|%20win-64&color=blue)
[![license](https://img.shields.io/github/license/miyako/ollama)](https://github.com/miyako/ollama/blob/main/LICENSE)
![downloads](https://img.shields.io/github/downloads/miyako/ollama/total)

# Use Ollama from 4D

## Abstract

[**Ollama**](https://github.com/ollama/ollama) is a lightweight, developer-friendly tool for running large language models (LLMs) locally. The backend is **llama.cpp** with a [**custom pipeline**](https://ollama.com/blog/multimodal-models) for multimodal models. 

Ollama has two main components:

- REST server
- llama.cpp runners

The REST server manages native inference subprocesses to handle loaded models.
Current upstream source also includes an MLX engine on supported platforms.

## Native server builds and installation

The reviewed source is **[Ollama v0.40.13](https://github.com/ollama/ollama/releases/tag/v0.40.13)**,
commit [`04088e7c9d41cba5d33bafbf788d87a465c79d5b`](https://github.com/ollama/ollama/commit/04088e7c9d41cba5d33bafbf788d87a465c79d5b),
verified as latest stable on 2026-11-02. **Build Ollama from source** now connects
the native matrix directly to the ordinary 4D **Publish** workflow. New component
ZIP/DMG releases contain every native architecture and runtime dependency; old
component archives such as `20.1.5` are not modified retroactively.

The **Build Ollama from source** GitHub Actions workflow builds the real CMake
superbuild, including the Go CLI/server, pinned llama.cpp helpers, libraries,
licenses and (Apple Silicon) MLX/Metal assets. It does not repackage official binaries.

<div class="table-wrap" markdown="1">

| Target | Payload | Limitations |
|---|---|---|
| macOS ARM64 | Metal + MLX Metal v3 | macOS 14+, no Metal v4 overlay |
| macOS Intel | CPU | macOS 14+, no Metal or MLX engine |
| Windows x64 | CPU and required runtime DLLs | Windows 10/11 UCRT, no MLX engine |

</div>

For 4D, install the ordinary component `ollama.zip`/`.dmg`; it already includes
these resources. For standalone use or manually updating an older component,
download the matching **unsigned native ZIP** and `.zip.sha256` from a
[`native-v…` release](https://github.com/miyako/ollama/releases).
Verify SHA-256 (`shasum -a 256 -c FILE.zip.sha256`, or compare Windows
`Get-FileHash FILE.zip -Algorithm SHA256` with the sidecar). Merge the extracted
platform directory into the component's `Resources/bin/`, keeping **all**
runtime libraries/assets and notices:

```text
Resources/bin/macOS/arm64/ollama
Resources/bin/macOS/arm64/lib/ollama/…
Resources/bin/macOS/x86_64/ollama-x86_64
Resources/bin/macOS/x86_64/lib/ollama/…
Resources/bin/Windows/ollama.exe
Resources/bin/Windows/lib/ollama/…
```

Install the updated 4D component source too: its launcher prefers the matching Mac
architecture executable, retaining the legacy flat-directory fallback when absent.
Restore executable permissions on macOS if your ZIP tool drops them.
Keep `BUILD-INFO.json`, `LICENSE`, runtime license notices and Windows compiler notices.
Native ZIPs remain unsigned; the normal component packaging signs its macOS code
and submits the component DMG for notarization. No desktop app, models, Linux/Windows ARM builds,
CUDA/ROCm/Vulkan payloads or model-backed inference verification are included.
Follow your organization's approval/signing process if OS security policy blocks
unsigned downloads. Model-backed inference and minimum-OS compatibility remain
separate deployment checks; a host API smoke alone does not establish inference compatibility.

For standalone use, run `OLLAMA_HOST=127.0.0.1:8080 macOS/arm64/ollama serve`,
or in PowerShell `$env:OLLAMA_HOST='127.0.0.1:8080'; .\Windows\ollama.exe serve`.
Check `curl --fail http://127.0.0.1:8080/api/version` without downloading a model.

PR/push builds use the reviewed immutable pin. Weekly/manual runs resolve the latest
published stable release once to a full SHA; manual `upstream_tag` and `expected_sha`
allow a specific stable revision. **Dispatch Build Ollama from source on `main`**:
it automatically builds, verifies and publishes new native inputs, or reuses the
already-published immutable source/recipe. Relevant main pushes and weekly runs
use the same integrated flow; PRs only validate and cannot publish.

The workflow then verifies all three complete consumer layouts and automatically
dispatches **Publish** if the component does not already contain the same native
payload. Component packaging stages verified assets at `Resources/bin`, checks the
actual built `.4dbase` and signed ZIP, then publishes ZIP/DMG, `native-source.json`
and `SHA256SUMS` together. The native `native-vVERSION-SHA12-rN` release stays **not latest**;
the intended integrated 4D component release is latest. An identical already-released
native/component pair skips both releases and the component version bump.
The parent dispatches without waiting, so the shared release lock cannot deadlock.
Complete unpublished drafts can be explicitly resumed from the native publishing
workflow using the original build run and draft IDs. Recovery verifies all nine
uploaded asset digests and the original source/recipe without replacing assets;
incomplete or unexpected drafts remain unpublished. See the README for recovery inputs.

Source checkouts retain their existing legacy executable/fallback behavior. Developers
can stage complete locked natives with `scripts/native/install.py prepare` followed by
`install`; see the README commands. Large runtime assets are staged at packaging time,
not committed to Git (`mlx.metallib` exceeds GitHub's 100 MB file limit).

For local builds, install Python 3.11+, CMake 3.24+, Ninja, Git/`gh` and the Go version
in upstream `go.mod` (1.26.10 at this pin), plus Xcode/Metal on Mac ARM or MSYS2 UCRT64
GCC/CMake/Ninja on Windows. Resolve with `python3 scripts/native/source.py --selection pinned`,
clone upstream at the recorded SHA, then run `python3 scripts/native/build.py`
(Windows: add `--runtime-licenses C:/msys64/ucrt64/share/licenses`).
[Full source-build, release, validation and follow-up proposal details](https://github.com/miyako/ollama#build-and-validate-locally).

## Usage

Instantiate `cs.ollama.ollama` in your *On Startup* database method:

```4d
var $ollama : cs.ollama.ollama

If (False)
    $ollama:=cs.ollama.ollama.new()  //default
Else 
    var $port : Integer
    
    var $event : cs.event.event
    $event:=cs.event.event.new()
    /*
        Function onError($params : Object; $error : cs.event.error)
        Function onSuccess($params : Object; $models : cs.event.models)
        Function onData($worker : 4D.SystemWorker; $params : Object)
        Function onTerminate($worker : 4D.SystemWorker; $params : Object)
    */
    
    $event.onError:=Formula(ALERT($2.message))
    $event.onSuccess:=Formula(ALERT($2.models.extract("name").join(",")+" pull finished"))  //not a readiness check; poll /api/tags
    $event.onData:=Formula(MESSAGE([$2.fileName; $2.percentage; "%"].join(" ")))
    $event.onTerminate:=Formula(LOG EVENT(Into 4D debug message; (["process"; $1.pid; "terminated!"].join(" "))))
    
    $port:=8080
    $models:=["nomic-embed-text:latest"; "llama3.2:1b"]
    
    $ollama:=cs.ollama.ollama.new($port; $models; {\
    host: "127.0.0.1"; \
    context_length: 4096; \
    keep_alive: "5m"; \
    max_loaded_models: 1; \
    max_queue: 100; \
    num_parallel: 10; \
    kv_cache_type: "f16"; \
    flash_attention: 1; \
    models: Folder(fk home folder).folder(".ollama/models")}; $event)
    
End if 
```

Unless a server is already running on the port, the constructor launches `ollama serve` and then runs `ollama pull` for each model. With no arguments it uses port `8080`, host `127.0.0.1` and the two models shown above. `event` may be `Null`, in which case no callbacks are delivered.

`onSuccess` is called once per model when its `ollama pull` process finishes; it does not check the exit status and does not mean the model is loaded (ollama loads a model on its first request). `onData` reports pull progress. `onTerminate` is not taken from `event`; the server process only calls a function passed as `options.onTerminate`.

Now you can test the server:

```
curl -X POST http://127.0.0.1:8080/v1/embeddings \
     -H "Content-Type: application/json" \
     -d '{"model":"nomic-embed-text:latest", 
     "input":"The quick brown fox jumps over the lazy dog."}'
```

```
curl -X POST http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "llama3.2:1b",
    "messages": [
      {"role": "user", "content": "Hello!"}
    ],
    "temperature": 0.7,
    "max_tokens": 100,
    "stream": true
  }'
```

Or, use AI Kit:

```4d
var $AIClient : cs.AIKit.OpenAI
$AIClient:=cs.AIKit.OpenAI.new()
$AIClient.baseURL:="http://127.0.0.1:8080/v1"

var $text : Text
$text:="The quick brown fox jumps over the lazy dog."

var $responseEmbeddings : cs.AIKit.OpenAIEmbeddingsResult
$responseEmbeddings:=$AIClient.embeddings.create($text)
```

Finally to terminate the server:

```4d
$ollama.terminate()
```

## AI Kit compatibility

The API is compatible with [Open AI](https://platform.openai.com/docs/api-reference/embeddings). 

<div class="table-wrap" markdown="1">

|Class|API|Availability|
|-|-|:-:|
|Models|`/v1/models`|✅|
|Chat|`/v1/chat/completions`|✅|
|Images|`/v1/images/generations`||
|Moderations|`/v1/moderations`||
|Embeddings|`/v1/embeddings`|✅|
|Files|`v1/files`||

</div>
