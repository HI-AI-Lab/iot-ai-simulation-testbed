// === Load Agent class ===
var Agent = Java.type('io.testbed.rl.Agent');
var ByteBuffer = Java.type("java.nio.ByteBuffer");
var ByteOrder  = Java.type("java.nio.ByteOrder");

// === Define feature mask (edit this for ablation) ===
// Example: only ETX on, everything else off
var mask = Java.to(
    [true,  // 0 ETX
     false, // 1 HC
     false, // 2 RE
     false, // 3 QLR
     false, // 4 BDI
     false, // 5 WR
     false, // 6 CC (not in node.c yet)
     false, // 7 PC
     false, // 8 SI
     false, // 9 GEN
     false, // 10 FWD
     false  // 11 QLOSS
    ],
    "boolean[]"
);

// === Instantiate Agent ===
var K = 4; // max candidate parents
var agent = new Agent(K, mask);

// === Helper: read/write memory with correct sizes ===
function getInt32(mote, varname) {
  var sym = mote.getMemory().getSymbolMap().get(varname);
  if (sym == null) throw "Variable not found: " + varname;
  var bytes = mote.getMemory().getMemorySegment(sym.addr, sym.size);
  var bb = ByteBuffer.wrap(bytes).order(ByteOrder.LITTLE_ENDIAN);
  return bb.getInt();
}

function getInt16(mote, varname) {
  var sym = mote.getMemory().getSymbolMap().get(varname);
  if (sym == null) throw "Variable not found: " + varname;
  var bytes = mote.getMemory().getMemorySegment(sym.addr, sym.size);
  var bb = ByteBuffer.wrap(bytes).order(ByteOrder.LITTLE_ENDIAN);
  return bb.getShort() & 0xFFFF;
}

function getByte(mote, varname) {
  var sym = mote.getMemory().getSymbolMap().get(varname);
  if (sym == null) throw "Variable not found: " + varname;
  var bytes = mote.getMemory().getMemorySegment(sym.addr, sym.size);
  return bytes[0] & 0xFF;
}

function getDouble(mote, varname) {
  var sym = mote.getMemory().getSymbolMap().get(varname);
  if (sym == null) throw "Variable not found: " + varname;
  var bytes = mote.getMemory().getMemorySegment(sym.addr, sym.size);
  var bb = ByteBuffer.wrap(bytes).order(ByteOrder.LITTLE_ENDIAN);
  return bb.getDouble();
}

function setInt16(mote, varname, value) {
  var sym = mote.getMemory().getSymbolMap().get(varname);
  if (sym == null) throw "Variable not found: " + varname;
  var buf = ByteBuffer.allocate(sym.size).order(ByteOrder.LITTLE_ENDIAN);
  buf.putShort(value & 0xFFFF);
  mote.getMemory().setMemorySegment(sym.addr, buf.array());
}

function setInt8(mote, varname, value) {
  var sym = mote.getMemory().getSymbolMap().get(varname);
  if (sym == null) throw "Variable not found: " + varname;
  var buf = ByteBuffer.allocate(sym.size);
  buf.put(value & 0xFF);
  mote.getMemory().setMemorySegment(sym.addr, buf.array());
}

// === Controller loop ===
TIMEOUT(600000, log.testOK()); // 600s timeout

while (true) {
  YIELD();

  if (msg.startsWith("[INFO: App       ] AGENT_REQ")) {
    // Example log: AGENT_REQ node=7 cand=3:etx=192,5:etx=240

    // Parse node id
    var parts = msg.split(" ");
    var nodeId = parseInt(parts[2].split("=")[1]); // node=7

    // Parse candidate list
    var candStr = parts[3].replace("cand=", "");
    var candPairs = candStr.split(",");
    var candIds = [];
    var candETX = [];
    for (var i = 0; i < candPairs.length; i++) {
      if (candPairs[i].length == 0) continue;
      var kv = candPairs[i].split(":etx=");
      candIds.push(parseInt(kv[0]));
      candETX.push(parseInt(kv[1]) / 100.0); // scale back from x100
    }

    // === Build state matrix S[k][Factive] ===
    var S = Java.to(new Array(candIds.length), "double[][]");
    for (var i = 0; i < candIds.length; i++) {
      var row = [];
      for (var j = 0; j < mask.length; j++) {
        if (mask[j]) {
          var val = 0.0;
          switch (j) {
            case 0: val = candETX[i]; break;                               // ETX
            case 1: val = getInt16(mote, "status_rank"); break;            // HC
            case 2: val = getDouble(mote, "status_residual_energy"); break;// RE
            case 3: val = getDouble(mote, "status_qlr"); break;            // QLR
            case 4: val = getDouble(mote, "status_bdi"); break;            // BDI
            case 5: val = getDouble(mote, "status_wr"); break;             // WR
            case 6: val = 0.0; break; // CC placeholder
            case 7: val = getInt16(mote, "status_pc"); break;              // PC
            case 8: val = getInt32(mote, "status_parent_switches"); break; // SI
            case 9: val = getInt32(mote, "status_gen_count"); break;       // GEN
            case 10: val = getInt32(mote, "status_fwd_count"); break;      // FWD
            case 11: val = getInt32(mote, "status_qloss_count"); break;    // QLOSS
          }
          row.push(val);
        }
      }
      S[i] = Java.to(row, "double[]");
    }

    // === Build counters for reward ===
    var counters = new Agent.Counters();
    counters.generated = getInt32(mote, "status_gen_count");
    counters.delivered = getInt32(mote, "status_fwd_count");
    counters.dropped   = getInt32(mote, "status_qloss_count");
    counters.residualEnergy = getDouble(mote, "status_residual_energy");
    counters.etx = candETX.length > 0 ? Math.round(candETX[0]*100) : 1000;
    counters.hopCount = getInt16(mote, "status_rank");
    counters.rankViolations = getInt32(mote, "status_parent_switches");

    // === Call agent ===
    var valid = Java.to(new Array(candIds.length).fill(true), "boolean[]");
    var choice = agent.decide(nodeId, S, valid, counters);
    var chosenParent = candIds[choice];

    // === Write back decision ===
    setInt16(mote, "agent_parent", chosenParent);
    setInt8(mote, "agent_waiting", 0);

    log.log("AGENT_APPLY node=" + nodeId + " parent=" + chosenParent + "\n");
  }

  log.log(time + "\t" + id + "\t" + msg + "\n");
}
