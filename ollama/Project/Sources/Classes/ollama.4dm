property options : Object

Class constructor($port : Integer; $models : Collection; $options : Object; $event : cs:C1710.event.event)
	
	This:C1470.options:=$options#Null:C1517 ? $options : {}
	
	If (Value type:C1509(This:C1470.options.host)#Is text:K8:3) || (This:C1470.options.host="")
		This:C1470.options.host:="127.0.0.1"
	End if 
	
	var $ollama : cs:C1710.workers.worker
	$ollama:=cs:C1710.workers.worker.new(cs:C1710._server)
	
	If (Not:C34($ollama.isRunning($port)))
		
		If ($models=Null:C1517) || ($models.length=0)
			$models:=["nomic-embed-text:latest"; "llama3.2:1b"]
		End if 
		
		If ($port=0) || ($port<0) || ($port>65535)
			$port:=8080
		End if 
		
		This:C1470.options.models:=$models
		This:C1470.options.port:=$port
		
		This:C1470._main($port; $models; $options; $event)
		
	End if 
	
Function _onTCP($status : Object; $options : Object)
	
	If ($status.success)
		
		var $className : Text
		$className:=Split string:C1554(Current method name:C684; "."; sk trim spaces:K86:2).first()
		
		CALL WORKER:C1389($className; Formula:C1597(start); $options)
		
	Else 
		
		var $statuses : Text
		$statuses:="TCP port "+String:C10($status.port)+" is aready used by process "+$status.PID.join(",")
		var $error : cs:C1710.event.error
		$error:=cs:C1710.event.error.new(1; $statuses)
		
		If ($options.event#Null:C1517) && (OB Instance of:C1731($options.event; cs:C1710.event.event))
			$options.event.onError.call(This:C1470; $options; $error)
		End if 
		
	End if 
	
Function _main($port : Integer; $models : Collection; $options : Object; $event : cs:C1710.event.event)
	
	main({port: $port; models: $models; options: $options; event: $event}; This:C1470._onTCP)
	
Function terminate()
	
	var $ollama : cs:C1710.workers.worker
	$ollama:=cs:C1710.workers.worker.new(cs:C1710._server)
	$ollama.terminate()
	