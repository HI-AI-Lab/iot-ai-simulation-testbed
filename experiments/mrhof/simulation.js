// === Load Agent class ===
var Agent = Java.type('io.testbed.rl.Agent');
var ByteBuffer = Java.type("java.nio.ByteBuffer");
var ByteOrder  = Java.type("java.nio.ByteOrder");

// === Config ===
var K = 4;
var INIT_ENERGY = 2000.0;

// === Feature mask (ablation-friendly) ===
// Index mapping must match mote's status_* fields meaning.
var mask = Java.to(
  [true,  // 0 ETX
   true, // 1 HC
   true, // 2 RE
   true, // 3 QLR
   false, // 4 BDI
   false, // 5 WR
   false, // 6 CC
   false, // 7 PC
   false, // 8 SI   (we map to parent_switches just as placeholder earlier)
   false, // 9 GEN
   false, // 10 FWD
   false  // 11 QLOSS
  ],
  "boolean[]"
);

// === Instantiate Agent ===
var agent = new Agent(K, mask, INIT_ENERGY);

// === Low-level memory helpers ===
function symOf(mote, varname){
  var sym = mote.getMemory().getSymbolMap().get(varname);
  if (sym == null) throw "Variable not found: " + varname + " on mote " + mote.getID();
  return sym;
}
function getInt32(mote, varname){
  var s = symOf(mote, varname);
  var bytes = mote.getMemory().getMemorySegment(s.addr, s.size);
  return ByteBuffer.wrap(bytes).order(ByteOrder.LITTLE_ENDIAN).getInt();
}
function getInt16(mote, varname){
  var s = symOf(mote, varname);
  var bytes = mote.getMemory().getMemorySegment(s.addr, s.size);
  return ByteBuffer.wrap(bytes).order(ByteOrder.LITTLE_ENDIAN).getShort() & 0xFFFF;
}
function getByte(mote, varname){
  var s = symOf(mote, varname);
  var bytes = mote.getMemory().getMemorySegment(s.addr, s.size);
  return bytes[0] & 0xFF;
}
function getDouble(mote, varname){
  var s = symOf(mote, varname);
  var bytes = mote.getMemory().getMemorySegment(s.addr, s.size);
  return ByteBuffer.wrap(bytes).order(ByteOrder.LITTLE_ENDIAN).getDouble();
}
function setInt16(mote, varname, value){
  var s = symOf(mote, varname);
  var buf = ByteBuffer.allocate(s.size).order(ByteOrder.LITTLE_ENDIAN);
  buf.putShort(value & 0xFFFF);
  mote.getMemory().setMemorySegment(s.addr, buf.array());
}
function setInt8(mote, varname, value){
  var s = symOf(mote, varname);
  var buf = ByteBuffer.allocate(s.size);
  buf.put(value & 0xFF);
  mote.getMemory().setMemorySegment(s.addr, buf.array());
}

// === Array readers for neighbor tables ===
function getU8Array(mote, varname, n){
  var s = symOf(mote, varname);
  var bytes = mote.getMemory().getMemorySegment(s.addr, s.size);
  var arr = [];
  for (var i=0; i<n && i<bytes.length; i++) arr.push(bytes[i] & 0xFF);
  return arr;
}
function getU16Array(mote, varname, n){
  var s = symOf(mote, varname);
  var bytes = mote.getMemory().getMemorySegment(s.addr, s.size);
  var bb = ByteBuffer.wrap(bytes).order(ByteOrder.LITTLE_ENDIAN);
  var arr = [];
  for (var i=0; i<n && (i*2+1) < bytes.length; i++){
    arr.push(bb.getShort(i*2) & 0xFFFF);
  }
  return arr;
}

// === Build candidate features for one mote ===
function buildCandidateMatrixFor(mote, candIds, candEtx){
  var S = [];
  var hcArr = [], reArr = [], qlrArr = [];

  for (var r = 0; r < candIds.length; r++) {
    var row = [];
    var parentMote = sim.getMoteWithID(candIds[r]);

    // Prepare feature row according to mask
    for (var j = 0; j < mask.length; j++) {
      if (!mask[j]) continue;
      var val = 0.0;
      switch (j) {
        case 0:  val = candEtx[r]; break;                                   // ETX
        case 1:  val = parentMote ? getInt16(parentMote, "status_rank") : 0; break;  // HC (rank proxy)
        case 2:  val = parentMote ? getDouble(parentMote, "status_residual_energy") : 0; break;
        case 3:  val = parentMote ? getDouble(parentMote, "status_qlr") : 0; break;
        case 4:  val = parentMote ? getDouble(parentMote, "status_bdi") : 0; break;
        case 5:  val = parentMote ? getDouble(parentMote, "status_wr") : 0; break;
        case 6:  val = 0.0; break; // CC not wired
        case 7:  val = parentMote ? getInt16(parentMote, "status_pc") : 0; break;
        case 8:  val = parentMote ? getInt32(parentMote, "status_parent_switches") : 0; break;
        case 9:  val = parentMote ? getInt32(parentMote, "status_gen_count") : 0; break;
        case 10: val = parentMote ? getInt32(parentMote, "status_fwd_count") : 0; break;
        case 11: val = parentMote ? getInt32(parentMote, "status_qloss_count") : 0; break;
      }
      row.push(val);
    }

    S[r] = Java.to(row, "double[]");

    // ground-truth arrays (for Agent internals)
    if (parentMote) {
      hcArr.push(getInt16(parentMote, "status_rank"));
      reArr.push(getDouble(parentMote, "status_residual_energy"));
      qlrArr.push(getDouble(parentMote, "status_qlr"));
    } else {
      hcArr.push(0.0); reArr.push(0.0); qlrArr.push(0.0);
    }
  }

  return {
    SArr  : Java.to(S, "double[][]"),
    hcArr : Java.to(hcArr, "double[]"),
    reArr : Java.to(reArr, "double[]"),
    qlrArr: Java.to(qlrArr,"double[]")
  };
}

// === Decide & set parent for a single mote ===
function decideAndSetParentFor(mote){
  var id = mote.getID();
  if (id === 1) return; // never set for sink

  var nn = getByte(mote, "status_num_neighbors") | 0;
  if (nn <= 0) {
    // No neighbors yet → clear waiting and skip
    setInt8(mote, "agent_waiting", 0);
    return;
  }

  var candIdsU8  = getU8Array(mote, "status_neighbor_ids", K);
  var candEtxU16 = getU16Array(mote, "status_etx_x100",   K);

  // Trim to nn and map to floats
  var candIds = [];
  var candEtx = [];
  for (var i=0; i<nn && i<candIdsU8.length && i<candEtxU16.length; i++){
    candIds.push(candIdsU8[i] | 0);
    candEtx.push((candEtxU16[i] | 0) / 100.0);
  }

  if (candIds.length === 0) {
    setInt8(mote, "agent_waiting", 0);
    return;
  }

  // Build feature matrix for these candidates
  var mats = buildCandidateMatrixFor(mote, candIds, candEtx);

  // Valid actions (one per listed neighbor)
  var validBool = [];
  for (var v=0; v<candIds.length; v++) validBool.push(true);
  var valid = Java.to(validBool, "boolean[]");

  // Requesting node counters
  var ctrs = new Agent.Counters();
  ctrs.generated      = getInt32(mote, "status_gen_count");
  ctrs.delivered      = getInt32(mote, "status_fwd_count");
  ctrs.dropped        = getInt32(mote, "status_qloss_count");
  ctrs.residualEnergy = getDouble(mote, "status_residual_energy");
  ctrs.etx            = (candEtxU16.length > 0) ? (candEtxU16[0] | 0) : 0;
  ctrs.hopCount       = getInt16(mote, "status_rank");
  ctrs.rankViolations = getInt32(mote, "status_parent_switches");

  // Decide
  var SArr       = mats.SArr;
  var candIdsArr = Java.to(candIds, "int[]");
  var choice = agent.decide(id, SArr, valid, ctrs,
                            candIdsArr, mats.hcArr, mats.reArr, mats.qlrArr);

  var choice = 1;

  // Index -> parent ID
  var idx = (typeof choice === "number") ? (choice|0) : 0;
  if (idx < 0 || idx >= candIds.length) idx = 0;
  var chosenParent = candIds[idx] || 0;

  // Write to mote
  setInt16(mote, "agent_parent", chosenParent);
  setInt8(mote, "agent_waiting", 0);

  // Optional log (commented to reduce console noise)
  // log.log("ASSIGN node=" + id + " parent=" + chosenParent + " cand=" + JSON.stringify(candIds) + "\n");
}

// === Global "assign all" ===
function assignParentsAll(){
  var count = sim.getMotesCount();
  for (var i=0; i<count; i++){
    var m = sim.getMote(i);
    if (!m) continue;
    var id = m.getID();
    if (id === 1) continue; // skip sink
    try {
      decideAndSetParentFor(m);
    } catch(e){
      // Defensive: don't kill controller on one bad mote
      log.log("ASSIGN_ERROR node=" + id + " err=" + e + "\n");
    }
  }
}

// === Controller main loop ===
TIMEOUT(6000000, log.testOK());

while (true) {
  YIELD();
  // ----- Phase control from sink -----
  if (msg.indexOf("ALL_NODES_TRAIN") >= 0) {
    // Initial assignment at start
    assignParentsAll();
    log.log("CTRL: INIT_ASSIGN done\n");
    continue;
  }
  if (msg.indexOf("ALL_NODES_RETRAIN") >= 0) {
    // End phase + train + re-assign
    agent.endPhase();
    assignParentsAll();
    continue;
  }
  // Always keep a raw log line if you want
  log.log(time + "\t" + id + "\t" + msg + "\n");
}
