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
function getInt32(mote, varname){ var sym=mote.getMemory().getSymbolMap().get(varname); if(sym==null) throw "Variable not found: "+varname; var bytes=mote.getMemory().getMemorySegment(sym.addr, sym.size); var bb=ByteBuffer.wrap(bytes).order(ByteOrder.LITTLE_ENDIAN); return bb.getInt(); }
function getInt16(mote, varname){ var sym=mote.getMemory().getSymbolMap().get(varname); if(sym==null) throw "Variable not found: "+varname; var bytes=mote.getMemory().getMemorySegment(sym.addr, sym.size); var bb=ByteBuffer.wrap(bytes).order(ByteOrder.LITTLE_ENDIAN); return bb.getShort() & 0xFFFF; }
function getByte(mote, varname){ var sym=mote.getMemory().getSymbolMap().get(varname); if(sym==null) throw "Variable not found: "+varname; var bytes=mote.getMemory().getMemorySegment(sym.addr, sym.size); return bytes[0] & 0xFF; }
function getDouble(mote, varname){ var sym=mote.getMemory().getSymbolMap().get(varname); if(sym==null) throw "Variable not found: "+varname; var bytes=mote.getMemory().getMemorySegment(sym.addr, sym.size); var bb=ByteBuffer.wrap(bytes).order(ByteOrder.LITTLE_ENDIAN); return bb.getDouble(); }
function setInt16(mote, varname, value){ var sym=mote.getMemory().getSymbolMap().get(varname); if(sym==null) throw "Variable not found: "+varname; var buf=ByteBuffer.allocate(sym.size).order(ByteOrder.LITTLE_ENDIAN); buf.putShort(value & 0xFFFF); mote.getMemory().setMemorySegment(sym.addr, buf.array()); }
function setInt8(mote, varname, value){ var sym=mote.getMemory().getSymbolMap().get(varname); if(sym==null) throw "Variable not found: "+varname; var buf=ByteBuffer.allocate(sym.size); buf.put(value & 0xFF); mote.getMemory().setMemorySegment(sym.addr, buf.array()); }

// === Controller loop ===
TIMEOUT(60000, log.testOK());

while (true) {
  YIELD();

  // --- Children request parent selection ---
  if (msg.startsWith("[INFO: App       ] AGENT_REQ")) {
    var parts = msg.split(" ");
    var nodeId = parseInt(parts[2].split("=")[1]);

    var candStr = parts[3].replace("cand=", "");
    var candPairs = candStr.length ? candStr.split(",") : [];
    var candIds = [];
    var candETX = [];

    for (var i = 0; i < candPairs.length; i++) {
      var kv = candPairs[i].split(":etx=");
      if (kv.length !== 2) continue;
      candIds.push(parseInt(kv[0]));
      candETX.push(parseInt(kv[1]) / 100.0);
    }

    var S = Java.to(new Array(candIds.length), "double[][]");
    var hcArr = []; var reArr = []; var qlrArr = [];

    for (var i = 0; i < candIds.length; i++) {
      var parentMote = sim.getMoteWithID(candIds[i]);
      var row = [];

      for (var j = 0; j < mask.length; j++) {
        if (mask[j]) {
          var val = 0.0;
          switch (j) {
            case 0: val = candETX[i]; break;
            case 1: val = getInt16(parentMote, "status_rank"); break;
            case 2: val = getDouble(parentMote, "status_residual_energy"); break;
            case 3: val = getDouble(parentMote, "status_qlr"); break;
            case 4: val = getDouble(parentMote, "status_bdi"); break;
            case 5: val = getDouble(parentMote, "status_wr"); break;
            case 6: val = 0.0; break;
            case 7: val = getInt16(parentMote, "status_pc"); break;
            case 8: val = getInt32(parentMote, "status_parent_switches"); break;
            case 9: val = getInt32(parentMote, "status_gen_count"); break;
            case 10: val = getInt32(parentMote, "status_fwd_count"); break;
            case 11: val = getInt32(parentMote, "status_qloss_count"); break;
          }
          row.push(val);
        }
      }
      S[i] = Java.to(row, "double[]");

      // true values for reward
      hcArr.push(getInt16(parentMote, "status_rank"));
      reArr.push(getDouble(parentMote, "status_residual_energy"));
      qlrArr.push(getDouble(parentMote, "status_qlr"));
    }

    var counters = new Agent.Counters();
    counters.generated      = getInt32(mote, "status_gen_count");
    counters.delivered      = getInt32(mote, "status_fwd_count");
    counters.dropped        = getInt32(mote, "status_qloss_count");
    counters.residualEnergy = getDouble(mote, "status_residual_energy");
    counters.etx            = candETX.length > 0 ? Math.round(candETX[0]*100) : 1000;
    counters.hopCount       = getInt16(mote, "status_rank");
    counters.rankViolations = getInt32(mote, "status_parent_switches");

    var valid = Java.to(new Array(candIds.length).fill(true), "boolean[]");

    var choice = agent.decide(nodeId, S, valid, counters,
                              Java.to(hcArr, "double[]"),
                              Java.to(reArr, "double[]"),
                              Java.to(qlrArr, "double[]"));

    var chosenParent = candIds[choice != null ? choice : 0] || 0;
    setInt16(mote, "agent_parent", chosenParent);
    setInt8(mote, "agent_waiting", 0);
  }

  // --- Sink signals end of phase ---
  if (msg.startsWith("[INFO: App       ] END_PHASE")) {
    // Only sink should log this
    var parts = msg.split(" ");
    var sinkId = parseInt(parts[2].split("=")[1]); // e.g., "sink=1"
    if (sinkId === 1) { // adjust if your sink ID differs
      agent.endPhase();
      log.log("CTRL: endPhase triggered by sink " + sinkId + "\n");
    }
  }
  log.log(time + "\t" + id + "\t" + msg + "\n");
}