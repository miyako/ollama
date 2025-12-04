---
layout: default
---

![version](https://img.shields.io/badge/version-20%2B-E23089)
![platform](https://img.shields.io/static/v1?label=platform&message=mac-intel%20|%20mac-arm%20|%20win-64&color=blue)
[![license](https://img.shields.io/github/license/miyako/ollama)](LICENSE)
![downloads](https://img.shields.io/github/downloads/miyako/ollama/total)

# Use Ollama from 4D

#### Abstract

[**Ollama**](https://github.com/ollama/ollama) is a lightweight, developer-friendly tool for running large language models (LLMs) locally. The backend is a modifed version of llama.cpp. 

Ollama has `2` compoenents:

- REST server
- llama.cpp runners

By design, llama.cpp can only run `1` model at a time. The REST server automatically spawns llama.cpp runners as subprocesses to handle multiple models simultaneously.

#### Usage

Instantiate `cs.ollama.server` and call `.start()` in your *On Startup* database method:

```4d
var $folder : 4D.Folder
$folder:=Folder(Folder("/PACKAGE/").platformPath; fk platform path).parent.folder("models")

var $ollama : cs.ollama.server
$ollama:=cs.ollama.server.new()

var $isRunning : Boolean
$isRunning:=$ollama.isRunning()

$ollama.start({\
host: "127.0.0.1:8080"; \
content_length: 4096; \
keep_alive: "5m"; \
models: $folder})
```

Unless the server is already running (in which case the costructor does nothing), the following procedure runs in the background:

1. The specified model is download via HTTP
2. The `ollama` program is started

Now you can test the server:

```
curl -X POST http://127.0.0.1:8080/v1/embeddings \
     -H "Content-Type: application/json" \
     -d '{"input":"The quick brown fox jumps over the lazy dog."}'
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

You can pull a public model from ollama.com:

```4d
#DECLARE($params : Object)

If (Count parameters=0)
    
    CALL WORKER(1; Current method name; {})
    
Else 
    
    var $ollama : cs.ollama.ollama
    $ollama:=cs.ollama.ollama.new()
    $ollama.pull({name: "nomic-embed-text"; data: {message: "done!"}}; Formula(ALERT($2.context.message)))
    
End if 
```

If you have a `Modelfile`, you can add it to the list of models:

```4d
#DECLARE($params : Object)

If (Count parameters=0)
    
    CALL WORKER(1; Current method name; {})
    
Else 
    
    var $file : 4D.File
    $file:=Folder(Folder("/PACKAGE/").platformPath; fk platform path).parent.file("models/elyza-8b-q4_k_m/Modelfile")
    
    var $ollama : cs.ollama.ollama
    $ollama:=cs.ollama.ollama.new()
    $ollama.create({name: "elyza:jp8b"; file: $file; data: $file}; Formula(onResponse))
    
End if 
```

Finally to terminate the server:

```4d
var $llama : cs.ollama.server
$llama:=cs.ollama.server.new()
$llama.terminate()
```

#### AI Kit compatibility

The API is compatibile with [Open AI](https://platform.openai.com/docs/api-reference/embeddings). 

|Class|API|Availability|
|-|-|:-:|
|Models|`/v1/models`|✅|
|Chat|`/v1/chat/completions`|✅|
|Images|`/v1/images/generations`||
|Moderations|`/v1/moderations`||
|Embeddings|`/v1/embeddings`|✅|
|Files|`v1/files`||
