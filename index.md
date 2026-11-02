---
layout: default
---

![version](https://img.shields.io/badge/version-20%2B-E23089)
![platform](https://img.shields.io/static/v1?label=platform&message=mac-intel%20|%20mac-arm%20|%20win-64&color=blue)
[![license](https://img.shields.io/github/license/miyako/ollama)](LICENSE)
![downloads](https://img.shields.io/github/downloads/miyako/ollama/total)

# Use Ollama from 4D

#### Abstract

[**Ollama**](https://github.com/ollama/ollama) is a lightweight, developer-friendly tool for running large language models (LLMs) locally. The backend is **llama.cpp** with a [**custom pipeline**](https://ollama.com/blog/multimodal-models) for multimodal models. 

Ollama has two main components:

- REST server
- llama.cpp runners

The REST server manages native inference subprocesses to handle loaded models.
Current upstream source also includes an MLX engine on supported platforms.

#### Native server builds and installation

The reviewed source is **[Ollama v0.40.13](https://github.com/ollama/ollama/releases/tag/v0.40.13)**,
commit [`04088e7c9d41cba5d33bafbf788d87a465c79d5b`](https://github.com/ollama/ollama/commit/04088e7c9d41cba5d33bafbf788d87a465c79d5b),
verified as latest stable on 2026-11-02. Native releases are independent of the
4D component version `20.1.5`; the old component archives do not automatically
receive new native payloads.

The **Build Ollama from source** GitHub Actions workflow builds the real CMake
superbuild, including the Go CLI/server, pinned llama.cpp helpers, libraries,
licenses and (Apple Silicon) MLX/Metal assets. It does not repackage official binaries.

| Target | Payload | Limitations |
|---|---|---|
| macOS ARM64 | Metal + MLX Metal v3 | macOS 14+, no Metal v4 overlay |
| macOS Intel | CPU | macOS 14+, no Metal or MLX engine |
| Windows x64 | CPU and required runtime DLLs | Windows 10/11 UCRT, no MLX engine |

Download the matching **unsigned native ZIP** and `.zip.sha256` from a
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
No Developer ID signing/notarization, desktop app, models, Linux/Windows ARM builds,
CUDA/ROCm/Vulkan payloads or model-backed inference verification are included.
Follow your organization's approval/signing process if OS security policy blocks
unsigned downloads. Intel/Windows native CI and 4D integration must be verified
before deployment; a host API smoke alone does not establish inference compatibility.

For standalone use, run `OLLAMA_HOST=127.0.0.1:8080 macOS/arm64/ollama serve`,
or in PowerShell `$env:OLLAMA_HOST='127.0.0.1:8080'; .\Windows\ollama.exe serve`.
Check `curl --fail http://127.0.0.1:8080/api/version` without downloading a model.

PR/push builds use the reviewed immutable pin. Weekly/manual runs resolve the latest
published stable release once to a full SHA; manual `upstream_tag` and `expected_sha`
allow a specific stable revision. Weekly builds only produce reviewable artifacts.
To publish, dispatch from this repository's `main` with `publish=true`: all three
native builds and isolated API checks must succeed, then a new `native-vVERSION-SHA12-rN`
release is created without overwriting existing tags/assets or replacing the latest
4D release. The publishing job alone receives write permission.
Complete unpublished drafts can be explicitly resumed from the native publishing
workflow using the original build run and draft IDs. Recovery verifies all nine
uploaded asset digests and the original source/recipe without replacing assets;
incomplete or unexpected drafts remain unpublished. See the README for recovery inputs.

For local builds, install Python 3.11+, CMake 3.24+, Ninja, Git/`gh` and the Go version
in upstream `go.mod` (1.26.10 at this pin), plus Xcode/Metal on Mac ARM or MSYS2 UCRT64
GCC/CMake/Ninja on Windows. Resolve with `python3 scripts/native/source.py --selection pinned`,
clone upstream at the recorded SHA, then run `python3 scripts/native/build.py`
(Windows: add `--runtime-licenses C:/msys64/ucrt64/share/licenses`).
[Full source-build, release, validation and follow-up proposal details](https://github.com/miyako/ollama#build-and-validate-locally).

#### Usage

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
    $event.onSuccess:=Formula(ALERT($2.models.extract("name").join(",")+" loaded!"))
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

#### AI Kit compatibility

The API is compatible with [Open AI](https://platform.openai.com/docs/api-reference/embeddings). 

|Class|API|Availability|
|-|-|:-:|
|Models|`/v1/models`|✅|
|Chat|`/v1/chat/completions`|✅|
|Images|`/v1/images/generations`||
|Moderations|`/v1/moderations`||
|Embeddings|`/v1/embeddings`|✅|
|Files|`v1/files`||
