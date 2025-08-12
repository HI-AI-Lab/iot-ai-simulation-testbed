/* Capture all mote output exactly as COOJA provides it (msg),
 * writing to unified and/or per-mote files using LogScriptEngine's log.append().
 *
 * NOTES:
 * - log.append() creates files if they don't exist, but NOT directories.
 *   So use filenames in the current working directory unless you’ve pre-created folders.
 * - 'msg' is exactly the GUI's Mote Output line, e.g. "[INFO: Main ] Hello".
 * - We don't add timestamps unless you toggle WRITE_TIME_PREFIX (default: false).
 */

var DURATION_MS = 60000;          // how long to run before we declare success
var WRITE_UNIFIED = true;         // write all messages to a single file?
var UNIFIED_FILE = "all_motes.log";

var WRITE_PER_MOTE = true;        // write one file per mote?
var PER_MOTE_PREFIX = "mote";     // filenames like mote01.log
var PER_MOTE_PAD = 2;             // pad width for IDs: 01, 02, ...

var WRITE_TIME_PREFIX = false;    // if true, prefix each line with simulation time
                                  // (kept off so per-mote files are raw)

function pad(n, w) {
  var s = "" + n;
  while (s.length < w) s = "0" + s;
  return s;
}

function lineForUnified(id, time, msg) {
  if (WRITE_TIME_PREFIX) return time + "\tID:" + id + "\t" + msg + "\n";
  return "ID:" + id + "\t" + msg + "\n";
}

function lineForPerMote(time, msg) {
  if (WRITE_TIME_PREFIX) return time + "\t" + msg + "\n";
  return msg + "\n"; // raw line (as-is)
}

/* On timeout, mark test OK (change to log.testFailed() if you want failure instead) */
TIMEOUT(DURATION_MS, function() {
  log.log("Capture finished after " + DURATION_MS + " ms\n");
  log.testOK();
});

/* Initial note in COOJA.testlog so you know we started */
log.log("Starting capture of all mote outputs...\n");

/* Main loop: every time a mote prints something, we get (id, time, msg) */
while (true) {
  YIELD(); // wait for a mote output event

  // msg is EXACTLY what GUI "Mote output" shows
  if (WRITE_UNIFIED) {
    log.append(UNIFIED_FILE, lineForUnified(id, time, msg));
  }
  if (WRITE_PER_MOTE) {
    var fname = PER_MOTE_PREFIX + pad(id, PER_MOTE_PAD) + ".log";
    log.append(fname, lineForPerMote(time, msg));
  }

  // Optional: also mirror to COOJA.testlog (comment out if you don't want it)
  // log.log("ID:" + id + " " + msg + "\n");
}
