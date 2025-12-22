//%attributes = {"invisible":true,"preemptive":"capable"}
#DECLARE($options : Object)

cs:C1710.workers.worker.new(cs:C1710._server).start($options.options.port; $options.options)

var $ollama : cs:C1710._server
$ollama:=cs:C1710._server.new(cs:C1710._Install_Controller)

$lines:=$ollama.install($options.event; $options; Formula:C1597(onModel))