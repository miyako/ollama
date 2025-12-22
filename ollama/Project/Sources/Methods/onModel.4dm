//%attributes = {}
#DECLARE($worker : 4D:C1709.SystemWorker; $params : Object)

var $model : cs:C1710.event.model
$model:=cs:C1710.event.model.new($params.context; True:C214)

var $models : cs:C1710.event.models
$models:=cs:C1710.event.models.new([$model])

var $instance : cs:C1710._server
$instance:=This:C1470.instance

If (OB Instance of:C1731($instance.onSuccess; 4D:C1709.Function))
	$instance.onSuccess.call(This:C1470; $worker; $models)
End if 