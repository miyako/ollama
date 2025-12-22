var $ollama : cs:C1710.ollama

If (False:C215)
	$ollama:=cs:C1710.ollama.new()  //default
Else 
	var $port : Integer
	
	var $event : cs:C1710.event.event
	$event:=cs:C1710.event.event.new()
/*
Function onError($params : Object; $error : cs.event.error)
Function onSuccess($params : Object; $models : cs.event.models)
Function onData($worker : 4D.SystemWorker; $params : Object)
Function onTerminate($worker : 4D.SystemWorker; $params : Object)
*/
	
	$event.onError:=Formula:C1597(ALERT:C41($2.message))
	$event.onSuccess:=Formula:C1597(ALERT:C41($2.models.extract("name").join(",")+" loaded!"))
	$event.onData:=Formula:C1597(MESSAGE:C88([$2.fileName; $2.percentage; "%"].join(" ")))
	$event.onTerminate:=Formula:C1597(LOG EVENT:C667(Into 4D debug message:K38:5; (["process"; $1.pid; "terminated!"].join(" "))))
	
	$port:=8080
	$models:=["nomic-embed-text:latest"; "llama3.2:1b"]
	
	$ollama:=cs:C1710.ollama.new($port; $models; {\
		host: "127.0.0.1"; \
		context_length: 4096; \
		keep_alive: "5m"; \
		max_loaded_models: 1; \
		max_queue: 100; \
		num_parallel: 10; \
		kv_cache_type: "f16"; \
		flash_attention: 1; \
		models: Folder:C1567(fk home folder:K87:24).folder(".ollama/models")}; $event)
	
End if 