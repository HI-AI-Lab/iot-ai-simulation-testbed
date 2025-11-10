// === Load Agent class ===
var Agent      = Java.type('io.testbed.rl.Agent');
var ByteBuffer = Java.type('java.nio.ByteBuffer');
var ByteOrder  = Java.type('java.nio.ByteOrder');

// ======== CONFIG ========
var K = 4;
var INIT_ENERGY = 2000.0;

// Path to YAML config (change if needed)
var MASK_PATH = "/workspace/testbed/mask.yaml";

// RPL constants (match firmware)
var RPL_ROOT_RANK      = 256;
var RPL_MIN_HOPRANKINC = 256;

// ======== YAML LOADER (minimal, for this schema) ========
function loadMaskYaml(path) {
  var Files = Java.type('java.nio.file.Files');
  var Paths = Java.type('java.nio.file.Paths');
  var StandardCharsets = Java.type('java.nio.charset.StandardCharsets');

  var text = null;
  try {
    text = new java.lang.String(Files.readAllBytes(Paths.get(path)), StandardCharsets.UTF_8);
  } catch (e) {
    log.log("MASK: could not read " + path + " (" + e + "). Using fallback mask.\n");
    return null; // signal fallback
  }

  // defaults (fallback baseline if something is missing)
  var cfg = {
    run: { id: "unspecified", notes: "" },
    features: {
      // baseline: turn everything OFF unless overridden
      all: false,
      etx:false, hc:false, re:false, qlr:false,
      bdi:false, wr:false, rssi:false, pc:false, si:false,
      gen:false, fwd:false, qloss:false
    }
  };

  // super-simple YAML parser for our 2-level structure
  var lines = ("" + text).split(/\r?\n/);
  var section = null;
  for (var li = 0; li < lines.length; li++) {
    var raw = lines[li];
    var l = raw.replace(/\t/g, "  ");
    // strip comments (keep '#' in notes by only stripping if a space precedes it)
    var hash = l.indexOf(" #");
    if (hash >= 0) l = l.substring(0, hash);
    l = l.trim();
    if (!l) continue;

    if (l === "run:" || l === "features:") { section = l.slice(0, -1); continue; }

    var m = l.match(/^([A-Za-z0-9_\-]+)\s*:\s*(.*)$/);
    if (!m) continue;
    var key = m[1].toLowerCase();
    var valRaw = m[2];

    // parse boolean / string
    var val;
    if (/^(true|false)$/i.test(valRaw)) {
      val = /^true$/i.test(valRaw);
    } else {
      // strip surrounding quotes for strings if any
      val = valRaw.replace(/^"(.*)"$/, "$1").replace(/^'(.*)'$/, "$1");
    }

    if (section === "run") {
      if (key === "id")    cfg.run.id = "" + val;
      if (key === "notes") cfg.run.notes = "" + val;
    } else if (section === "features") {
      if (key === "all") cfg.features.all = !!val;
      else if (key in cfg.features) cfg.features[key] = !!val;
      // unknown keys ignored
    }
  }

  // apply "all" baseline then overrides already done
  return cfg;
}

function buildMaskFromConfig(cfg) {
  // feature order MUST match Agent & controller:
  // 0 ETX, 1 HC, 2 RE, 3 QLR, 4 BDI, 5 WR, 6 RSSI, 7 PC, 8 SI, 9 GEN, 10 FWD, 11 QLOSS
  var order = ["etx","hc","re","qlr","bdi","wr","rssi","pc","si","gen","fwd","qloss"];

  // start from all=false, unless features.all==true then flip all true, then override with explicit keys
  var f = {};
  for (var i=0;i<order.length;i++) f[order[i]] = cfg && cfg.features && cfg.features.all ? true : false;
  if (cfg && cfg.features) {
    for (var k in cfg.features) {
      if (k === "all") continue;
      if (f.hasOwnProperty(k)) f[k] = !!cfg.features[k];
    }
  }

  var maskArr = [];
  var enabledNames = [];
  for (var j=0;j<order.length;j++) {
    var on = !!f[order[j]];
    maskArr.push(on);
    if (on) enabledNames.push(order[j]);
  }

  var rid = cfg ? cfg.run.id : "unspecified";
  var rnotes = cfg ? cfg.run.notes : "";
  log.log("MASK_CONFIG run.id=" + rid + " notes=\"" + rnotes + "\" on=" + enabledNames.join(",") + "\n");
  log.log("MASK_CONFIG effective=" + maskArr.map(function(b){return b?1:0;}).join("") + " (k="+K+")\n");
  return Java.to(maskArr, "boolean[]");
}

// ======== Helpers to read/write mote memory ========
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
  return (ByteBuffer.wrap(bytes).order(ByteOrder.LITTLE_ENDIAN).getShort() & 0xFFFF);
}
function getI16(mote, varname){
  var s = symOf(mote, varname);
  var bytes = mote.getMemory().getMemorySegment(s.addr, s.size);
  return ByteBuffer.wrap(bytes).order(ByteOrder.LITTLE_ENDIAN).getShort();
}
function getByte(mote, varname){
  var s = symOf(mote, varname);
  var bytes = mote.getMemory().getMemorySegment(s.addr, s.size);
  return (bytes[0] & 0xFF);
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

// Arrays
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
  for (var i=0; i<n && (i*2+1) < bytes.length; i++) arr.push(bb.getShort(i*2) & 0xFFFF);
  return arr;
}
function getI16Array(mote, varname, n){
  var s = symOf(mote, varname);
  var bytes = mote.getMemory().getMemorySegment(s.addr, s.size);
  var bb = ByteBuffer.wrap(bytes).order(ByteOrder.LITTLE_ENDIAN);
  var arr = [];
  for (var i=0; i<n && (i*2+1) < bytes.length; i++) arr.push(bb.getShort(i*2));
  return arr;
}

// Unsigned coercion for JS (keep 32-bit counters non-negative)
function u32(x){ return (x >>> 0); }

// RPL rank -> hop-count
function rankToHops(rank){
  if (!Number.isFinite(rank) || rank <= 0) return 0;
  if (rank < RPL_ROOT_RANK) return 0;
  return Math.max(0, Math.floor((rank - RPL_ROOT_RANK) / RPL_MIN_HOPRANKINC));
}

// RSSI dBm -> [0..10], 0x7fff (unknown) -> 0
function rssiTo0to10(dbm){
  if (dbm === 0x7fff || !Number.isFinite(dbm)) return 0.0;
  var v = (dbm + 100) / 7.0; // -100 -> 0, ~-30 -> ~10
  if (v < 0) v = 0; if (v > 10) v = 10;
  return v;
}

// ======== Build candidate features for one mote ========
function buildCandidateMatrixFor(mote, candIds, candEtx){
  var S = [];
  var hcArr = [], reArr = [], qlrArr = [];

  // per-candidate RSSI array from the requester
  var candRssiI16 = getI16Array(mote, "status_link_rssi_dbm", K);

  for (var r = 0; r < candIds.length; r++) {
    var row = [];
    var parentMote = sim.getMoteWithID(candIds[r]);

    // feature order must match mask / Agent indices
    for (var j = 0; j < mask.length; j++) {
      if (!mask[j]) continue;
      var val = 0.0;
      switch (j) {
        case 0:  val = candEtx[r]; break; // ETX (path ETX from requester)
        case 1:  val = parentMote ? rankToHops(getInt16(parentMote, "status_rank")) : 0; break;
        case 2:  val = parentMote ? getDouble(parentMote, "status_residual_energy") : 0; break;
        case 3:  val = parentMote ? getDouble(parentMote, "status_qlr") : 0; break;
        case 4:  val = parentMote ? getDouble(parentMote, "status_bdi") : 0; break;
        case 5:  val = parentMote ? getDouble(parentMote, "status_wr") : 0; break;
        case 6:  val = rssiTo0to10( (r < candRssiI16.length) ? candRssiI16[r] : 0x7fff ); break;
        case 7:  val = parentMote ? getInt16(parentMote, "status_pc") : 0; break;
        case 8:  val = parentMote ? getInt32(parentMote, "status_parent_switches") : 0; break;
        case 9:  val = parentMote ? getInt32(parentMote, "status_gen_count") : 0; break;
        case 10: val = parentMote ? getInt32(parentMote, "status_fwd_count") : 0; break;
        case 11: val = parentMote ? getInt32(parentMote, "status_qloss_count") : 0; break;
      }
      row.push(val);
    }

    S[r] = Java.to(row, "double[]");

    // Ground-truth arrays for reward (from parent snapshot)
    if (parentMote) {
      hcArr.push(rankToHops(getInt16(parentMote, "status_rank")));
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

// ======== Decide & set parent for a single mote ========
function decideAndSetParentFor(mote){
  var mid = mote.getID();
  if (mid === 1) return; // sink

  var nn = getByte(mote, "status_num_neighbors") | 0;
  if (nn <= 0) { setInt8(mote, "agent_waiting", 0); return; }

  var candIdsU8  = getU8Array(mote, "status_neighbor_ids", K);
  var candEtxU16 = getU16Array(mote, "status_etx_x100",   K);

  // Trim to nn and map to floats
  var candIds = [];
  var candEtx = [];
  for (var i=0; i<nn && i<candIdsU8.length && i<candEtxU16.length; i++){
    candIds.push(candIdsU8[i] | 0);
    candEtx.push((candEtxU16[i] | 0) / 100.0);
  }
  if (candIds.length === 0) { setInt8(mote, "agent_waiting", 0); return; }

  // Build features
  var mats = buildCandidateMatrixFor(mote, candIds, candEtx);

  // Valid actions (one per candidate)
  var validBool = [];
  for (var v=0; v<candIds.length; v++) validBool.push(true);
  var valid = Java.to(validBool, "boolean[]");

  // Requesting-node counters
  var ctrs = new Agent.Counters();
  ctrs.generated      = u32(getInt32(mote, "status_gen_count"));
  ctrs.delivered      = u32(getInt32(mote, "status_fwd_count"));
  ctrs.dropped        = u32(getInt32(mote, "status_qloss_count"));
  ctrs.residualEnergy = getDouble(mote, "status_residual_energy");
  ctrs.hopCount       = rankToHops(getInt16(mote, "status_rank"));
  ctrs.rankViolations = u32(getInt32(mote, "status_parent_switches"));

  // min path ETX*100 across candidates (proxy)
  var minEtx100 = 0;
  for (var i2=0; i2<nn && i2<candEtxU16.length; i2++){
    var v = (candEtxU16[i2] | 0);
    if (i2===0 || v < minEtx100) minEtx100 = v;
  }
  ctrs.etx = minEtx100;

  // Decide (RL)
  var SArr       = mats.SArr;
  var candIdsArr = Java.to(candIds, "int[]");

  // Optional: sanity warn if row lengths deviate from Factive (Agent also warns)
  if (SArr.length > 0) {
    var row0 = SArr[0];
    // (cannot see Factive here; Agent will log exact Factive vs row length)
  }

  var choice = agent.decide(mid, SArr, valid, ctrs, candIdsArr, mats.hcArr, mats.reArr, mats.qlrArr);

  // Index -> parent ID
  var idx = (typeof choice === "number") ? (choice|0) : 0;
  if (idx < 0 || idx >= candIds.length) idx = 0;
  var chosenParent = (candIds[idx] | 0);
  if (!chosenParent) { setInt8(mote, "agent_waiting", 0); return; }

  // Write to mote
  setInt16(mote, "agent_parent", chosenParent);
  setInt8(mote, "agent_waiting", 0);
}

// ======== Global "assign all" ========
function assignParentsAll(){
  var count = sim.getMotesCount();
  for (var i=0; i<count; i++){
    var m = sim.getMote(i);
    if (!m) continue;
    var mid = m.getID();
    if (mid === 1) continue; // skip sink
    try { decideAndSetParentFor(m); }
    catch(e){ log.log("ASSIGN_ERROR node=" + mid + " err=" + e + "\n"); }
  }
}

// ======== Build mask from YAML (with fallback) & start Agent ========
var cfg = loadMaskYaml(MASK_PATH);
var mask = cfg ? buildMaskFromConfig(cfg) :
  // Fallback: ETX+HC+RE+QLR+RSSI (safe default for your heavy-load study)
  Java.to([true,true,true,true,false,false,true,false,false,false,false,false], "boolean[]");

var agent = new Agent(K, mask, INIT_ENERGY);

// ======== Controller main loop ========
TIMEOUT(6000000, log.testOK());

while (true) {
  YIELD();
  if (msg.indexOf("ALL_NODES_TRAIN") >= 0) {
    assignParentsAll();
    log.log("CTRL: INIT_ASSIGN done\n");
    continue;
  }
  if (msg.indexOf("ALL_NODES_RETRAIN") >= 0) {
    agent.endPhase();
    assignParentsAll();
    continue;
  }
  log.log(time + "\t" + id + "\t" + msg + "\n");
}
