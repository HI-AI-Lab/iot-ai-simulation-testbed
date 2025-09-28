// === Load Agent class ===
var Agent = Java.type('io.testbed.rl.Agent');
var ByteBuffer = Java.type("java.nio.ByteBuffer");
var ByteOrder  = Java.type("java.nio.ByteOrder");

// === Define feature mask (edit this for ablation) ===
var mask = Java.to(
  [true,  // 0 ETX
   false, // 1 HC
   false, // 2 RE
   false, // 3 QLR
   false, // 4 BDI
   false, // 5 WR
   false, // 6 CC
   false, // 7 PC
   false, // 8 SI
   false, // 9 GEN
   false, // 10 FWD
   false  // 11 QLOSS
  ],
  "boolean[]"
);

// === Instantiate Agent ===
var K = 4;
var INIT_ENERGY = 2000.0;
var agent = new Agent(K, mask, INIT_ENERGY);

// === Helpers ===
function getInt32(mote, varname){
  var sym = mote.getMemory().getSymbolMap().get(varname);
  if (sym == null) throw "Variable not found: " + varname;
  var bytes = mote.getMemory().getMemorySegment(sym.addr, sym.size);
  var bb = ByteBuffer.wrap(bytes).order(ByteOrder.LITTLE_ENDIAN);
  return bb.getInt();
}
function getInt16(mote, varname){
  var sym = mote.getMemory().getSymbolMap().get(varname);
  if (sym == null) throw "Variable not found: " + varname;
  var bytes = mote.getMemory().getMemorySegment(sym.addr, sym.size);
  var bb = ByteBuffer.wrap(bytes).order(ByteOrder.LITTLE_ENDIAN);
  return bb.getShort() & 0xFFFF;
}
function getByte(mote, varname){
  var sym = mote.getMemory().getSymbolMap().get(varname);
  if (sym == null) throw "Variable not found: " + varname;
  var bytes = mote.getMemory().getMemorySegment(sym.addr, sym.size);
  return bytes[0] & 0xFF;
}
function getDouble(mote, varname){
  var sym = mote.getMemory().getSymbolMap().get(varname);
  if (sym == null) throw "Variable not found: " + varname;
  var bytes = mote.getMemory().getMemorySegment(sym.addr, sym.size);
  var bb = ByteBuffer.wrap(bytes).order(ByteOrder.LITTLE_ENDIAN);
  return bb.getDouble();
}
function setInt16(mote, varname, value){
  var sym = mote.getMemory().getSymbolMap().get(varname);
  if (sym == null) throw "Variable not found: " + varname;
  var buf = ByteBuffer.allocate(sym.size).order(ByteOrder.LITTLE_ENDIAN);
  buf.putShort(value & 0xFFFF);
  mote.getMemory().setMemorySegment(sym.addr, buf.array());
}
function setInt8(mote, varname, value){
  var sym = mote.getMemory().getSymbolMap().get(varname);
  if (sym == null) throw "Variable not found: " + varname;
  var buf = ByteBuffer.allocate(sym.size);
  buf.put(value & 0xFF);
  mote.getMemory().setMemorySegment(sym.addr, buf.array());
}

// === Controller loop ===
TIMEOUT(600000, log.testOK());

while (true) {
  YIELD();

  // --- Children request parent selection ---
  if (msg.startsWith("[INFO: App       ] AGENT_REQ")) {
    // Robust parse
    var parts = msg.trim().split(/\s+/);
    var nodeTok = null, candTok = null;
    for (var p = 0; p < parts.length; p++) {
      if (parts[p].indexOf("node=") === 0) nodeTok = parts[p];
      if (parts[p].indexOf("cand=") === 0) candTok = parts[p];
    }

    var nodeId = nodeTok ? parseInt(nodeTok.substring(5)) : id;
    var candStr = candTok ? candTok.substring(5) : "";
    var candPairs = candStr.length ? candStr.split(",") : [];

    var candIds = [];
    var candETX = [];
    for (var i = 0; i < candPairs.length; i++) {
      // "NN:etx=XYZ"
      var pos = candPairs[i].indexOf(":etx=");
      if (pos <= 0) continue;
      var pid = parseInt(candPairs[i].substring(0, pos));
      var petx = parseInt(candPairs[i].substring(pos + 5));
      if (!isNaN(pid) && !isNaN(petx)) {
        candIds.push(pid);
        candETX.push(petx / 100.0);
      }
    }

    if (candIds.length === 0) {
      //log.log("AGENT_CHOICE: node=" + nodeId + " candIds=[] (no neighbors) — skipping decision\n");
      setInt8(mote, "agent_waiting", 0);
      //log.log(time + "\t" + id + "\t" + msg + "\n");
      continue;
    }

    // Build state rows for real candidates (agent pads internally to K)
    var S = [];
    var hcArr = [], reArr = [], qlrArr = [];
    for (var r = 0; r < candIds.length; r++) {
      var parentMote = sim.getMoteWithID(candIds[r]);
      var row = [];
      if (parentMote != null) {
        for (var j = 0; j < mask.length; j++) {
          if (!mask[j]) continue;
          var val = 0.0;
          switch (j) {
            case 0:  val = candETX[r]; break;
            case 1:  val = getInt16(parentMote, "status_rank"); break;
            case 2:  val = getDouble(parentMote, "status_residual_energy"); break;
            case 3:  val = getDouble(parentMote, "status_qlr"); break;
            case 4:  val = getDouble(parentMote, "status_bdi"); break;
            case 5:  val = getDouble(parentMote, "status_wr"); break;
            case 6:  val = 0.0; break; // CC not wired
            case 7:  val = getInt16(parentMote, "status_pc"); break;
            case 8:  val = getInt32(parentMote, "status_parent_switches"); break;
            case 9:  val = getInt32(parentMote, "status_gen_count"); break;
            case 10: val = getInt32(parentMote, "status_fwd_count"); break;
            case 11: val = getInt32(parentMote, "status_qloss_count"); break;
          }
          row.push(val);
        }
        // per-candidate ground truth (for reward inside Agent)
        hcArr.push(getInt16(parentMote, "status_rank"));
        reArr.push(getDouble(parentMote, "status_residual_energy"));
        qlrArr.push(getDouble(parentMote, "status_qlr"));
      } else {
        // Defensive: missing mote → zeros
        for (var j = 0; j < mask.length; j++) if (mask[j]) row.push(0.0);
        hcArr.push(0.0); reArr.push(0.0); qlrArr.push(0.0);
      }
      S[r] = Java.to(row, "double[]"); // convert each row
    }

    // Convert outer structures
    var SArr      = Java.to(S, "double[][]");
    var candIdsArr= Java.to(candIds, "int[]");
    var hcArrJ    = Java.to(hcArr,  "double[]");
    var reArrJ    = Java.to(reArr,  "double[]");
    var qlrArrJ   = Java.to(qlrArr, "double[]");

    // Valid actions = number of real candidates
    var validArr = [];
	for (var i = 0; i < candIds.length; i++) {
	  validArr[i] = true;
	}
	var valid = Java.to(validArr, "boolean[]");

    // Counters for the requesting node (optional)
    var counters = new Agent.Counters();
    counters.generated      = getInt32(mote, "status_gen_count");
    counters.delivered      = getInt32(mote, "status_fwd_count");
    counters.dropped        = getInt32(mote, "status_qloss_count");
    counters.residualEnergy = getDouble(mote, "status_residual_energy");
    counters.etx            = Math.round(candETX[0] * 100); // coarse ETX snapshot
    counters.hopCount       = getInt16(mote, "status_rank");
    counters.rankViolations = getInt32(mote, "status_parent_switches");

    // Decide
    var choice = agent.decide(nodeId, SArr, valid, counters,
                              candIdsArr, hcArrJ, reArrJ, qlrArrJ);

    // Map index -> parent ID safely
    var idx = (typeof choice === "number") ? (choice|0) : 0;
    if (idx < 0 || idx >= candIds.length) idx = 0;
    var chosenParent = candIds[idx] || 0;
    /*
    log.log("AGENT_CHOICE: node=" + nodeId +
            " choiceIdx=" + idx +
            " chosenParent=" + chosenParent +
            " candIds=" + JSON.stringify(candIds) + "\n");*/

    setInt16(mote, "agent_parent", chosenParent);
    setInt8(mote, "agent_waiting", 0);
  }

  // --- Sink signals end of phase ---
  if (msg.startsWith("[INFO: App       ] END_PHASE")) {
    agent.endPhase();
    //log.log("CTRL: endPhase triggered by sink\n");
  }

  // Raw line too
  //log.log(time + "\t" + id + "\t" + msg + "\n");
}
