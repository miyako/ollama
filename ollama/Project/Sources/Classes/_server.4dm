Class extends _ollama

Class constructor($controller : 4D:C1709.Class)
	
	Super:C1705($controller)
	
Function start($option : Object) : 4D:C1709.SystemWorker
	
	This:C1470.bind($option; ["onTerminate"])
	
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
			: (["models"; "debug"; "version"; "help"].includes($arg.key))
				continue
			: ($arg.key="host")
				$OLLAMA_HOST:=String:C10($arg.value)+":"+String:C10($option.port)
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
	
	This:C1470.controller.variables.OLLAMA_HOST:=$OLLAMA_HOST
	This:C1470.controller.variables.HOME:=Folder:C1567(fk home folder:K87:24).path
	This:C1470.controller.variables.GIN_MODE:="release"
	
	//SET TEXT TO PASTEBOARD($command)
	
	return This:C1470.controller.execute($command; Null:C1517; $option.data).worker
	
Function get controller()->$controller : cs:C1710._Normal_Controller
	
	$controller:=This:C1470._controller
	
Function _simple($function : Text; $option : Variant; $formula : 4D:C1709.Function) : Collection
	
	var $stdOut; $isStream; $isAsync : Boolean
	var $options : Collection
	var $results : Collection
	$results:=[]
	
	Case of 
		: (Value type:C1509($option)=Is object:K8:27)
			$options:=[$option]
		: (Value type:C1509($option)=Is collection:K8:32)
			$options:=$option
		Else 
			$options:=[]
	End case 
	
	var $commands : Collection
	$commands:=[]
	
	If (OB Instance of:C1731($formula; 4D:C1709.Function))
		$isAsync:=True:C214
		This:C1470.controller.onResponse:=$formula
	End if 
	
	For each ($option; $options)
		
		If ($option=Null:C1517) || (Value type:C1509($option)#Is object:K8:27)
			continue
		End if 
		
		$stdOut:=Not:C34(OB Instance of:C1731($option.output; 4D:C1709.File))
		
		$command:=This:C1470.escape(This:C1470.executablePath)
		$command+=" "
		$command+=$function
		
		If (Value type:C1509($option.model)=Is text:K8:3) && ($option.model#"")
			$command+=" "
			$command+=This:C1470.escape($option.model)
		Else 
			continue  //mandatory
		End if 
		
		$OLLAMA_HOST:="127.0.0.1:8080"
		
		var $arg : Object
		var $valueType : Integer
		var $key : Text
		
		For each ($arg; OB Entries:C1720($option))
			Case of 
				: (["port"; "model"; "debug"].includes($arg.key))
					continue
				: ($arg.key="host")
					$OLLAMA_HOST:=String:C10($arg.value)+":"+String:C10($option.port)
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
		
		This:C1470.controller.variables.OLLAMA_HOST:=$OLLAMA_HOST
		This:C1470.controller.variables.HOME:=Folder:C1567(fk home folder:K87:24).path
		This:C1470.controller.variables.GIN_MODE:="release"
		
		//SET TEXT TO PASTEBOARD($command)
		
		var $worker : 4D:C1709.SystemWorker
		$worker:=This:C1470.controller.execute($command; Null:C1517; $option.model).worker
		
		If (Not:C34($isAsync))
			$worker.wait()
		End if 
		
		If ($stdOut) && (Not:C34($isAsync))
			$results.push(This:C1470.controller.stdOut)
			This:C1470.controller.clear()
		End if 
		
	End for each 
	
	If ($stdOut) && (Not:C34($isAsync))
		return $results
	End if 
	
Function install($params : Object; $option : Variant; $formula : 4D:C1709.Function) : Collection
	
	This:C1470.bind($params; ["onSuccess"; "onData"])
	
	$option.models:=$option.models.map(Formula:C1597($1.result:={model: $1.value; port: $2; host: $3}); $option.port; $option.options.host)
	
	return This:C1470._simple("pull"; $option.models; $formula)
	