// Java NIO helpers (add these at the top!)
var ByteBuffer = Java.type("java.nio.ByteBuffer");
var ByteOrder  = Java.type("java.nio.ByteOrder");

function getInt(mote, varname) {
  var sym = mote.getMemory().getSymbolMap().get(varname);
  if (sym == null) throw "Variable not found: " + varname;
  var bytes = mote.getMemory().getMemorySegment(sym.addr, sym.size);
  var bb = ByteBuffer.wrap(bytes).order(ByteOrder.LITTLE_ENDIAN);
  return bb.getInt();
}

function setInt(mote, varname, value) {
  var sym = mote.getMemory().getSymbolMap().get(varname);
  if (sym == null) throw "Variable not found: " + varname;
  var buf = ByteBuffer.allocate(sym.size).order(ByteOrder.LITTLE_ENDIAN);
  buf.putInt(value);
  mote.getMemory().setMemorySegment(sym.addr, buf.array());
}

TIMEOUT(6000, log.testOK());

YIELD();

log.log("JavaScript Total motes in sim: " + sim.getMotes().length + "\n");

/*
while (true) {
  YIELD();
  //log.log(""+msg+"\n");
  
  if(msg.startsWith("[INFO: App       ] toggle_value:")){
	  var toggle_value = getInt(mote, "toggle_value");
	  log.log("mote_id: " +id+"- \t" + "JS: " + toggle_value +" MOTE: " +msg+ "\n");
	  toggle_value++;
	  setInt(mote, "toggle_value", toggle_value);
  }
  //log.log(time + "\t" + id + "\t" + msg + "\n");
}
*/