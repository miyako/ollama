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

Ollama has `2` compoenents:

- REST server
- llama.cpp runners

By design, llama.cpp can only run `1` model at a time. The REST server automatically spawns llama.cpp runners as subprocesses to handle multiple models simultaneously.

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
