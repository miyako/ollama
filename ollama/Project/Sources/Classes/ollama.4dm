Class extends _CLI

Class constructor($controller : 4D:C1709.Class)
	
	If (Not:C34(OB Instance of:C1731($controller; cs:C1710._ollama_Controller)))
		$controller:=cs:C1710._ollama_Controller
	End if 
	
	Super:C1705("ollama"; $controller)
	
Function get worker() : 4D:C1709.SystemWorker
	
	return This:C1470.controller.worker
	
Function terminate()
	
	This:C1470.controller.terminate()
	
Function create($option : Object; $formula : 4D:C1709.Function)
	
	If (Not:C34(cs:C1710.server.new().isRunning()))
		return 
	End if 
	
	var $isAsync : Boolean
	
	If (OB Instance of:C1731($formula; 4D:C1709.Function))
		$isAsync:=True:C214
		This:C1470.controller.onResponse:=$formula
	End if 
	
	var $command : Text
	$command:=This:C1470.escape(This:C1470.executablePath)
	$command+=" create"
	
	If (Value type:C1509($option.name)=Is text:K8:3) && ($option.name#"")
		$command+=" "
		$command+=This:C1470.escape($option.name)
	End if 
	
	Case of 
		: (Value type:C1509($option.file)=Is object:K8:27) && (OB Instance of:C1731($option.file; 4D:C1709.File)) && ($option.file.exists)
			$command+=" -f "
			$command+=This:C1470.expand($option.file).path
	End case 
	
	This:C1470.controller.variables.HOME:=Folder:C1567(fk home folder:K87:24).path
	This:C1470.controller.variables.GIN_MODE:="release"
	This:C1470.controller.variables.OLLAMA_HOST:=Storage:C1525.variables.OLLAMA_HOST
	
	var $worker : 4D:C1709.SystemWorker
	$worker:=This:C1470.controller.execute($command; Null:C1517; $option.data).worker
	
	var $stdErr : Text
	
	If (Not:C34($isAsync))
		$worker.wait()
	End if 
	
	If (Not:C34($isAsync))
		$stdErr:=$worker.responseError
	End if 
	
	If (Not:C34($isAsync))
		return $stdErr
	End if 
	
Function list($option : Object) : Collection
	
	If (Not:C34(cs:C1710.server.new().isRunning()))
		return 
	End if 
	
	var $command : Text
	$command:=This:C1470.escape(This:C1470.executablePath)
	$command+=" list"
	
	This:C1470.controller.variables.HOME:=Folder:C1567(fk home folder:K87:24).path
	This:C1470.controller.variables.GIN_MODE:="release"
	This:C1470.controller.variables.OLLAMA_HOST:=Storage:C1525.variables.OLLAMA_HOST
	
	$status:=This:C1470.controller.execute($command).worker
	
	$status.wait()
	
	var $stdOut : Text
	$stdOut:=$status.response
	
	$models:=[]
	
	ARRAY LONGINT:C221($pos; 0)
	ARRAY LONGINT:C221($len; 0)
	
	var $model : Text
	$list:=Split string:C1554($stdOut; This:C1470.EOL)
	$list.shift()
	For each ($model; $list)
		If (Match regex:C1019("^(\\S+)\\s+(\\S+)\\s+(\\S+\\s+\\S+)\\s+(.+?)\\s+$"; $model; 1; $pos; $len))
			$name:=Substring:C12($model; $pos{1}; $len{1})
			$id:=Substring:C12($model; $pos{2}; $len{2})
			$size:=Substring:C12($model; $pos{3}; $len{3})
			$modified:=Substring:C12($model; $pos{4}; $len{4})
			$models.push({name: $name; id: $id; size: $size; modified: $modified})
		End if 
	End for each 
	
	return $models
	
Function serve($option : Object) : 4D:C1709.SystemWorker
	
	var $command : Text
	$command:=This:C1470.escape(This:C1470.executablePath)
	$command+=" serve"
	
	Case of 
		: (Value type:C1509($option.models)=Is object:K8:27) && (OB Instance of:C1731($option.models; 4D:C1709.Folder)) && ($option.models.exists)
			This:C1470.controller.variables.OLLAMA_MODELS:=This:C1470.expand($option.models).path
	End case 
	
	$OLLAMA_HOST:="127.0.0.1:8080"
	
	var $arg : Object
	var $valueType : Integer
	var $key : Text
	
	For each ($arg; OB Entries:C1720($option))
		Case of 
			: (["models"; "debug"].includes($arg.key))
				continue
			: ($arg.key="host")
				$OLLAMA_HOST:=String:C10($arg.value)
				continue
		End case 
		$valueType:=Value type:C1509($arg.value)
		$env:="OLLAMA_"+Uppercase:C13($arg.key; *)
		Case of 
			: ($valueType=Is real:K8:4)
				This:C1470.controller.variables[$env]:=String:C10($arg.value)
			: ($valueType=Is text:K8:3)
				This:C1470.controller.variables[$env]:=$arg.value
			: ($valueType=Is boolean:K8:9) && ($arg.value)
				This:C1470.controller.variables[$env]:="1"
			Else 
				//
		End case 
	End for each 
	
	Use (Storage:C1525)
		Storage:C1525.variables:=New shared object:C1526("OLLAMA_HOST"; $OLLAMA_HOST)
	End use 
	
	This:C1470.controller.variables.OLLAMA_HOST:=$OLLAMA_HOST
	This:C1470.controller.variables.HOME:=Folder:C1567(fk home folder:K87:24).path
	This:C1470.controller.variables.GIN_MODE:="release"
	
	return This:C1470.controller.execute($command).worker