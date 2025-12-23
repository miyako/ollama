property stdOut : Text
property stdErr : Text

Class extends _Normal_Controller

Class constructor($CLI : cs:C1710._CLI)
	
	Super:C1705($CLI)
	
	This:C1470.clear()
	
Function onDataError($worker : 4D:C1709.SystemWorker; $params : Object)
	
	Super:C1706.onDataError($worker; $params)
	
	var $instance : cs:C1710._server
	$instance:=This:C1470.instance
	
	If ($instance.onData#Null:C1517) && (OB Instance of:C1731($instance.onData; 4D:C1709.Function))
		
		var $stdErr : Text
		$stdErr:=This:C1470.stdErr
		
		ARRAY LONGINT:C221($pos; 0)
		ARRAY LONGINT:C221($len; 0)
		$i:=1
		While (Match regex:C1019("pulling\\s(.+):\\s*(\\d+)%"; $stdErr; $i; $pos; $len))
			$fileName:=Substring:C12($stdErr; $pos{1}; $len{1})
			$percentage:=Num:C11(Substring:C12($stdErr; $pos{2}; $len{2}))
			$i:=$pos{0}+$len{0}
			$context:={}
			$context.fileName:=$fileName
			$context.percentage:=$percentage
			$instance.onData.call(This:C1470; $worker; $context)
		End while 
		This:C1470.stdErr:=Substring:C12($stdErr; $i)
	End if 
	