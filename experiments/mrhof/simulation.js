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

while (true) {
  YIELD();
  log.log(time + "\t" + id + "\t" + getInt(mote, "toggle_value") + "\n");
  //log.log(time + "\t" + id + "\t" + msg + "\n");
}
