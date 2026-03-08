// === Agent class ===
var Agent      = Java.type('io.testbed.rl.Agent');
var ByteBuffer = Java.type('java.nio.ByteBuffer');
var ByteOrder  = Java.type('java.nio.ByteOrder');

// === Constants ===
var K = 4;
var INIT_ENERGY = 2000.0;
var MASK_PATH = java.lang.System.getenv("MASK_FILE");
if(MASK_PATH === null || (""+MASK_PATH).trim().length === 0) {
  MASK_PATH = "/workspace/testbed/mask.yaml";
}

var RPL_ROOT_RANK      = 256;
var RPL_MIN_HOPRANKINC = 256;

// ===================== DEBUG (single node, once per phase) =====================
// This is SAFE: it only prints for one node, once per TRAIN and once per RETRAIN.
var DEBUG_NODE_ID = 5;          // trace only this node
var DEBUG_ON = true;
var END_MS = 6000000; 

var _phase = "NONE";            // "TRAIN" or "RETRAIN"
var _dbgTrainDone = false;
var _dbgRetrainDone = false;

var totalDecisions = 0;
var decisionsByNode = {};         // mid -> count
var _printedDecisionSummary = false;
var _globalStopTriggered = false;
var _globalStopNode = -1;
var _loggedRetrainSkip = false;

function dbgOnce(mid, mats, candIds, candEtx, valid, idxChosen) {
  if (!DEBUG_ON) return;
  if (mid !== DEBUG_NODE_ID) return;

  if (_phase === "TRAIN" && _dbgTrainDone) return;
  if (_phase === "RETRAIN" && _dbgRetrainDone) return;

  var SArr = mats.SArr;
  var row0 = (SArr && SArr.length > 0) ? SArr[0] : null;
  var rowLen = row0 ? row0.length : -1;

  // short row dump (limit 12 vals)
  var row0Str = "";
  if (row0) {
    var lim = Math.min(row0.length, 12);
    var tmp = [];
    for (var i=0; i<lim; i++) tmp.push("" + row0[i]);
    row0Str = tmp.join(",");
  }

  var chosenParent = (idxChosen >= 0 && idxChosen < candIds.length) ? candIds[idxChosen] : 0;

  log.log(
    "DBG node=" + mid +
    " phase=" + _phase +
    " nn=" + candIds.length +
    " candIds=[" + candIds.join(",") + "]" +
    " candEtx=[" + candEtx.map(function(x){return x.toFixed(2)}).join(",") + "]" +
    " valid=[" + valid.map(function(b){return b?1:0}).join("") + "]" +
    " Factive=" + rowLen +
    " row0=[" + row0Str + "]" +
    " idx=" + idxChosen +
    " parent=" + chosenParent +
    "\n"
  );

  if (_phase === "TRAIN") _dbgTrainDone = true;
  if (_phase === "RETRAIN") _dbgRetrainDone = true;
}

function printNNMaxSummaryOnce(){
  if(_printedNNMaxSummary) return;
  if(time < END_MS - 1000) return;   // print in last 1s of sim

  var N = sim.getMotesCount();
  var c0=0,c1=0,c2=0,c3=0,c4=0,c5=0;

  for(var i=0;i<N;i++){
    var m = sim.getMote(i);
    if(!m || m.getID()===1) continue;
    var mid = m.getID();
    var mx = nnMaxByNode[mid];
    if(mx === undefined) mx = 0;

    if(mx<=0) c0++;
    else if(mx===1) c1++;
    else if(mx===2) c2++;
    else if(mx===3) c3++;
    else if(mx===4) c4++;
    else c5++;
  }

  log.log("NN_MAX_SUMMARY: maxNN=" + globalNNMax + " atNode=" + globalNNMaxNode +
          " perNodeMaxHist{0:" + c0 + ",1:" + c1 + ",2:" + c2 + ",3:" + c3 + ",4:" + c4 + ",5+:" + c5 + "}\n");

  _printedNNMaxSummary = true;
}

// ======================================================================
//  MASK YAML LOADER (supports 13 metrics)
// ======================================================================
function loadMaskYaml(path){
  var Files = Java.type("java.nio.file.Files");
  var Paths = Java.type("java.nio.file.Paths");
  var Charset = Java.type("java.nio.charset.StandardCharsets");

  var text = null;
  try {
    text = new java.lang.String(Files.readAllBytes(Paths.get(path)), Charset.UTF_8);
  } catch(e){
    log.log("MASK: cannot read " + path + ". Using fallback.\n");
    return null;
  }

  var cfg = {
    run:{ id:"unspecified", notes:"" },
    features:{
      all:false,
      // LINK
      etx:false, rssi:false, pfi:false,
      // NODE
      re:false, bdi:false, qo:false, qlr:false, hc:false,
      si:false, tv:false, pc:false,
      // NETWORK
      wr:false, str:false
    }
  };

  var lines = (""+text).split(/\r?\n/);
  var section = null;

  for(var li=0; li<lines.length; li++){
    var L = lines[li].replace(/\t/g,"  ");
    var hash = L.indexOf(" #");
    if(hash>=0) L = L.substring(0,hash);
    L = L.trim();
    if(!L) continue;

    if(L==="run:" || L==="features:"){
      section = L.slice(0,-1);
      continue;
    }

    var m = L.match(/^([A-Za-z0-9_\-]+)\s*:\s*(.*)$/);
    if(!m) continue;

    var key = m[1].toLowerCase();
    var raw = m[2];

    var val = (/^(true|false)$/i.test(raw))
              ? /^true$/i.test(raw)
              : raw.replace(/^"(.*)"$/,"$1").replace(/^'(.*)'$/,"$1");

    if(section==="run"){
      if(key==="id") cfg.run.id = ""+val;
      if(key==="notes") cfg.run.notes = ""+val;
    }
    else if(section==="features"){
      if(cfg.features.hasOwnProperty(key)) cfg.features[key] = !!val;
    }
  }

  return cfg;
}

// ======================================================================
// FIXED FEATURE ORDER (must match node.c + agent.java)
// ======================================================================
var ORDER = [
  "etx","rssi","pfi",        // LINK (3)
  "re","bdi","qo","qlr","hc","si","tv","pc",   // NODE (8)
  "wr","str"                // NETWORK (2)
];

// ======================================================================
// MASK BUILDER
// ======================================================================
function buildMaskFromConfig(cfg){
  var f = {};
  var all = cfg.features.all;

  for(var i=0;i<ORDER.length;i++)
    f[ORDER[i]] = all ? true : false;

  for(var k in cfg.features){
    if(k==="all") continue;
    if(f.hasOwnProperty(k)) f[k] = cfg.features[k];
  }

  var maskArr = [];
  var en = [];

  for(var j=0;j<ORDER.length;j++){
    var on = f[ORDER[j]];
    maskArr.push(on);
    if(on) en.push(ORDER[j]);
  }

  if(DEBUG_ON){
  log.log(
    "MASK_CONFIG run=" + cfg.run.id +
    " enabled={" + en.join(",") + "}\n"
  );
  log.log("MASK_BITS=" + maskArr.map(function(x){return x?1:0;}).join("") + "\n");
  }

  return Java.to(maskArr,"boolean[]");
}

// ======================================================================
// MEMORY HELPERS
// ======================================================================
function symOf(m, name){
  var s = m.getMemory().getSymbolMap().get(name);
  if(!s) throw "Symbol not found: " + name;
  return s;
}

// Safe getters: DO NOT break existing setup.
// If a symbol doesn't exist (because a feature isn't compiled/exported), return 0.
function getInt32(m,n){
  var s=symOf(m,n);
  var b=m.getMemory().getMemorySegment(s.addr,s.size);
  return ByteBuffer.wrap(b).order(ByteOrder.LITTLE_ENDIAN).getInt();
}
function getInt16(m,n){
  var s=symOf(m,n);
  var b=m.getMemory().getMemorySegment(s.addr,s.size);
  return ByteBuffer.wrap(b).order(ByteOrder.LITTLE_ENDIAN).getShort() & 0xFFFF;
}
function getInt8(m,n){
  var s=symOf(m,n);
  var b=m.getMemory().getMemorySegment(s.addr,s.size);
  return b[0] & 0xFF;
}
function getDouble(m,n){
  var s=symOf(m,n);
  var b=m.getMemory().getMemorySegment(s.addr,s.size);
  return ByteBuffer.wrap(b).order(ByteOrder.LITTLE_ENDIAN).getDouble();
}

function getInt320(m,n){ try { return getInt32(m,n); } catch(e){ return 0; } }
function getInt160(m,n){ try { return getInt16(m,n); } catch(e){ return 0; } }
function getInt80(m,n){  try { return getInt8(m,n);  } catch(e){ return 0; } }
function getDouble0(m,n){try { return getDouble(m,n);} catch(e){ return 0.0; } }

function getU8Array(m,n,K){
  var s=symOf(m,n);
  var b=m.getMemory().getMemorySegment(s.addr,s.size);
  var a=[];
  for(var i=0;i<K && i<b.length;i++) a.push(b[i]&0xFF);
  return a;
}
function getU16Array(m,n,K){
  var s=symOf(m,n);
  var b=m.getMemory().getMemorySegment(s.addr,s.size);
  var bb=ByteBuffer.wrap(b).order(ByteOrder.LITTLE_ENDIAN);
  var a=[];
  for(var i=0;i<K && (i*2+1)<b.length;i++){
    a.push(bb.getShort(i*2)&0xFFFF);
  }
  return a;
}
function getI16Array(m,n,K){
  var s=symOf(m,n);
  var b=m.getMemory().getMemorySegment(s.addr,s.size);
  var bb=ByteBuffer.wrap(b).order(ByteOrder.LITTLE_ENDIAN);
  var a=[];
  for(var i=0;i<K && (i*2+1)<b.length;i++){
    a.push(bb.getShort(i*2));  // signed int16_t
  }
  return a;
}

// Read contiguous double[] arrays (PFI is typically an array in C, best read this way).
function getDoubleArray(m, n, K){
  var s=symOf(m,n);
  var b=m.getMemory().getMemorySegment(s.addr,s.size);
  var bb=ByteBuffer.wrap(b).order(ByteOrder.LITTLE_ENDIAN);
  var a=[];
  for(var i=0;i<K && (i*8+7)<b.length;i++){
    a.push(bb.getDouble(i*8));
  }
  return a;
}

function getPFIArray(m){
  try { return getDoubleArray(m, "status_pfi", K); }
  catch(e){ return [0.0,0.0,0.0,0.0]; }
}

function setInt8(m, n, v){
  var s = symOf(m, n);
  var bb = ByteBuffer.allocate(1).order(ByteOrder.LITTLE_ENDIAN);
  bb.put(0, (v & 0xFF));
  m.getMemory().setMemorySegment(s.addr, bb.array());
}

function setInt16(m, n, v){
  var s = symOf(m, n);
  var bb = ByteBuffer.allocate(2).order(ByteOrder.LITTLE_ENDIAN);
  bb.putShort(0, v & 0xFFFF);
  m.getMemory().setMemorySegment(s.addr, bb.array());
}

function u32(x){ return (x>>>0); }

// ======================================================================
// Rank/Hop utilities
// ======================================================================
function rankToHops(rank){
  if(rank<RPL_ROOT_RANK) return 0;
  return Math.floor((rank - RPL_ROOT_RANK)/RPL_MIN_HOPRANKINC);
}
function rssiTo0to10(dbm){
  if(dbm===0x7fff || dbm===32767) return 0; // unknown sentinel
  var v=(dbm+100)/7.0;
  if(v<0) v=0; if(v>10) v=10;
  return v;
}

function printNNListOnce(){
  if(_printedNNList) return;
  if(_phase !== "RETRAIN") return;

  var N = sim.getMotesCount();
  var out = [];
  for(var i=0;i<N;i++){
    var m = sim.getMote(i);
    if(!m || m.getID()===1) continue;
    var nn = getInt80(m, "status_num_neighbors"); // safe getter you already have
    if(nn >= 2) out.push(m.getID() + ":" + nn);
  }

  log.log("NNGE2 count=" + out.length + " list=[" + out.join(",") + "]\n");
  _printedNNList = true;
}

function printNNHistogramOnce(){
  if(_printedNNList) return;     // reuse same one-time flag
  if(_phase !== "RETRAIN") return;

  var N = sim.getMotesCount();
  var c0=0,c1=0,c2=0,c3=0,c4=0,c5=0;
  for(var i=0;i<N;i++){
    var m = sim.getMote(i);
    if(!m || m.getID()===1) continue;
    var nn = getInt80(m,"status_num_neighbors");
    if(nn<=0) c0++;
    else if(nn===1) c1++;
    else if(nn===2) c2++;
    else if(nn===3) c3++;
    else if(nn===4) c4++;
    else c5++;
  }
  log.log("NN_HIST RETRAIN: nn0=" + c0 + " nn1=" + c1 + " nn2=" + c2 +
          " nn3=" + c3 + " nn4=" + c4 + " nn5p=" + c5 + "\n");
  _printedNNList = true;
}

function updateNNStats(){
  var N = sim.getMotesCount();
  for(var i=0;i<N;i++){
    var m = sim.getMote(i);
    if(!m || m.getID()===1) continue;

    var mid = m.getID();
    var nn = getInt80(m, "status_num_neighbors");

    // per-node max
    var old = nnMaxByNode[mid];
    if(old === undefined || nn > old) nnMaxByNode[mid] = nn;

    // global max
    if(nn > globalNNMax){
      globalNNMax = nn;
      globalNNMaxNode = mid;
    }
  }
}

function printDecisionSummaryOnce(){
  if(_printedDecisionSummary) return;
  if(time < END_MS - 1000) return;   // last 1s

  var keys = Object.keys(decisionsByNode).sort(function(a,b){return (+a)-(+b);});
  var parts = [];
  for(var i=0;i<keys.length;i++){
    var k = keys[i];
    parts.push(k + ":" + decisionsByNode[k]);
  }

  log.log("DECISION_SUMMARY total=" + totalDecisions +
          " nodes=" + keys.length +
          " perNode=[" + parts.join(",") + "]\n");
  _printedDecisionSummary = true;
}

function findFirstDeadNodeByEnergy(){
  var N = sim.getMotesCount();
  for(var i=0;i<N;i++){
    var m = sim.getMote(i);
    if(!m) continue;
    var mid = m.getID();
    if(mid === 1) continue; // sink

    var re = null;
    try { re = getDouble(m, "status_residual_energy"); } catch(e){ re = null; }
    if(re !== null && re <= 0.0) return mid;
  }
  return -1;
}

function maybeTriggerGlobalStopFromEnergy(){
  if(_globalStopTriggered) return;
  var deadNode = findFirstDeadNodeByEnergy();
  if(deadNode > 0) {
    broadcastGlobalStop(deadNode);
  }
}

function broadcastGlobalStop(triggerNodeId){
  if(_globalStopTriggered) return;
  _globalStopTriggered = true;
  _globalStopNode = triggerNodeId;

  var N = sim.getMotesCount();
  var flagged = 0;
  for(var i=0;i<N;i++){
    var m = sim.getMote(i);
    if(!m) continue;
    try{
      setInt8(m, "status_global_stop", 1);
      flagged++;
    } catch(e){
      // Some motes (e.g. sink firmware) may not export this symbol.
    }
  }

  log.log("CTRL: GLOBAL_STOP triggerNode=" + triggerNodeId +
          " flaggedMotes=" + flagged + "\n");
}

// ======================================================================
// BUILD FEATURE MATRIX (13 metrics, masked, PFI per-parent)
// ======================================================================
function buildCandidateMatrixFor(mote, candIds, candEtx){

  var S=[];
  var hcArr=[], reArr=[], qlrArr=[];
  var rssiArr = [];
  try { rssiArr = getI16Array(mote,"status_link_rssi_dbm",K); }
  catch(e){ rssiArr = [0x7fff,0x7fff,0x7fff,0x7fff]; }
  var pfiArr  = getPFIArray(mote);

  for(var r=0;r<candIds.length;r++){
    var row=[];
    var pid = candIds[r];
    var pm  = sim.getMoteWithID(pid);

    for(var j=0;j<mask.length;j++){
      if(!mask[j]) continue;

      var val=0;

      switch(j){

        // LINK --------------------------------------
        case 0: val = candEtx[r]; break;                                              // ETX
        case 1: val = rssiTo0to10((r < rssiArr.length) ? rssiArr[r] : 0x7fff); break; // RSSI
        case 2: val = (r < pfiArr.length) ? pfiArr[r] : 0.0; break;                  // PFI

        // NODE --------------------------------------
        case 3:  val = pm ? getDouble0(pm,"status_residual_energy") : 0; break;
        case 4:  val = pm ? getDouble0(pm,"status_bdi") : 0; break;
        case 5:  val = pm ? getInt320(pm,"status_qo") : 0; break;
        case 6:  val = pm ? getDouble0(pm,"status_qlr") : 0; break;
        case 7:  val = rankToHops(pm ? getInt160(pm,"status_rank") : 0); break;
        case 8:  val = pm ? getDouble0(pm,"status_si") : 0; break;
        case 9:  val = pm ? getDouble0(pm,"status_tv") : 0; break;
        case 10: val = pm ? getInt160(pm,"status_pc") : 0; break;

        // NETWORK -----------------------------------
        case 11: val = pm ? getDouble0(pm,"status_wr") : 0; break;
        case 12: val = pm ? getDouble0(pm,"status_str") : 0; break;
      }

      row.push(val);
    }

    S.push(Java.to(row,"double[]"));

    // Reward arrays (hc, re, qlr) from parent snapshot
    if(pm){
      hcArr.push(rankToHops(getInt160(pm,"status_rank")));
      reArr.push(getDouble0(pm,"status_residual_energy"));
      qlrArr.push(getDouble0(pm,"status_qlr"));
    } else {
      hcArr.push(0); reArr.push(0); qlrArr.push(0);
    }
  }

  return {
    SArr  : Java.to(S,"double[][]"),
    hcArr : Java.to(hcArr,"double[]"),
    reArr : Java.to(reArr,"double[]"),
    qlrArr: Java.to(qlrArr,"double[]")
  };
}

// ======================================================================
// DECISION ROUTINE
// ======================================================================
function decideAndSetParentFor(mote){

  var mid = mote.getID();
  if(mid===1) return;

  var nn = getInt80(mote, "status_num_neighbors");
  if(nn<=0){ setInt8(mote,"agent_waiting",0); return; }

  var ids = getU8Array(mote,"status_neighbor_ids",K);
  var exs = getU16Array(mote,"status_etx_x100",K);

  var candIds=[], candEtx=[];
  for(var i=0;i<nn && i<K;i++){       // safety: never exceed K
    candIds.push(ids[i]);
    candEtx.push(exs[i]/100.0);
  }
  
  if (DEBUG_ON && mid === DEBUG_NODE_ID) {
  log.log("DBG_IN node="+mid+" phase="+_phase+
          " nn="+nn+
          " ids=["+candIds.join(",")+"]"+
          " etx=["+candEtx.map(function(x){return x.toFixed(2)}).join(",")+"]\n");
  }

  if(candIds.length===0){ setInt8(mote,"agent_waiting",0); return; }

  var mats = buildCandidateMatrixFor(mote, candIds, candEtx);

  // valid must be length K, first candIds.length = true, others = false
  var valid = [];
  for (var i=0;i<K;i++){
    valid.push(i < candIds.length);
  }

  var ctrs = new Agent.Counters();
  ctrs.generated      = u32(getInt320(mote,"status_gen_count"));
  ctrs.delivered      = u32(getInt320(mote,"status_fwd_count"));
  ctrs.dropped        = u32(getInt320(mote,"status_qloss_count"));
  ctrs.residualEnergy = getDouble0(mote,"status_residual_energy");
  ctrs.hopCount       = rankToHops(getInt160(mote,"status_rank"));
  ctrs.rankViolations = u32(getInt320(mote,"status_parent_switches"));
  ctrs.etx            = (exs && exs.length>0) ? exs[0] : 0; // not used in Agent now, but fine

  totalDecisions++;
  decisionsByNode[mid] = (decisionsByNode[mid] || 0) + 1;

  var choice = agent.decide(
      mid,
      mats.SArr,
      Java.to(valid,"boolean[]"),
      ctrs,
      Java.to(candIds,"int[]"),
      mats.hcArr,
      mats.reArr,
      mats.qlrArr
  );

  var idx = (typeof choice==="number") ? (choice|0) : 0;
  if(idx<0 || idx>=candIds.length) idx=0;
  
  if (DEBUG_ON && mid === DEBUG_NODE_ID) {
  log.log("DBG_CHOICE node="+mid+" phase="+_phase+
          " idx="+idx+" parent="+candIds[idx]+"\n");
  }

  dbgOnce(mid, mats, candIds, candEtx, valid, idx);

  setInt16(mote,"agent_parent", candIds[idx]);
  
  var ap = getInt160(mote,"agent_parent");

  if (DEBUG_ON && mid === DEBUG_NODE_ID) {
    log.log("DBG_WB node="+mid+" wrote="+candIds[idx]+" readback="+ap+"\n");
  }
  
  setInt8(mote,"agent_waiting",0);
}

// ======================================================================
// MAIN LOOP
// ======================================================================
function assignParentsAll(){
  var N = sim.getMotesCount();
  for(var i=0;i<N;i++){
    var m = sim.getMote(i);
    if(!m || m.getID()===1) continue;
    try{ decideAndSetParentFor(m); }
    catch(e){ log.log("ERROR node="+m.getID()+" "+e+"\n"); }
  }
}

var cfg  = loadMaskYaml(MASK_PATH);
var mask = cfg ? buildMaskFromConfig(cfg)
               : Java.to([true,false,false,false,false,false,false,false,false,false,false,false,false],
                         "boolean[]");

var agent = new Agent(K, mask, INIT_ENERGY);

TIMEOUT(6000000, log.testOK());

while(true){
	YIELD();		
	
  if(msg.indexOf("ALL_NODES_TRAIN")>=0){
    _phase = "TRAIN";
    maybeTriggerGlobalStopFromEnergy();
    if(!_globalStopTriggered){
      assignParentsAll();
      log.log("CTRL: INIT_ASSIGN done\n");
    }
    continue;
  }
	if (msg.indexOf("ALL_NODES_RETRAIN") >= 0) {
	  _phase = "RETRAIN";
    maybeTriggerGlobalStopFromEnergy();
    if(_globalStopTriggered){
      if(!_loggedRetrainSkip){
        log.log("CTRL: RETRAIN skipped (global stop active)\n");
        _loggedRetrainSkip = true;
      }
      continue;
    }
    agent.endPhase();
    assignParentsAll();
    printDecisionSummaryOnce();
	  continue;
	}
	log.log(time+"\t"+id+"\t"+msg+"\n");
}
