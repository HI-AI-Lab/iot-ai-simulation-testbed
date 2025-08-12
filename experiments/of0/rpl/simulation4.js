/* Capture all mote output exactly as COOJA provides it (msg) */

var DURATION_MS = 60000;          // how long to run before success
var WRITE_UNIFIED = true;         // write all messages to a single file
var UNIFIED_FILE = "all_motes.log";

var WRITE_PER_MOTE = true;        // write one file per mote
var PER_MOTE_PREFIX = "mote";     // filenames like mote01.log
var PER_MOTE_PAD = 2;             // pad width for IDs: 01, 02, ...

var WRITE_TIME_PREFIX = false;    // keep raw by default

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

/* Set a timeout that logs a final note, then reports success.
   NOTE: second argument must be an expression, not a function. */
TIMEOUT(DURATION_MS, (log.log("Capture finished after " + DURATION_MS + " ms\n"), log.testOK()));

log.log("Starting capture of all mote outputs...\n");

/* Main loop: every time a mote prints something, we get (id, time, msg) */
while (true) {
  YIELD(); // wait for a mote output event

  if (WRITE_UNIFIED) {
    log.append(UNIFIED_FILE, lineForUnified(id, time, msg));
  }
  if (WRITE_PER_MOTE) {
    var fname = PER_MOTE_PREFIX + pad(id, PER_MOTE_PAD) + ".log";
    log.append(fname, lineForPerMote(time, msg));
  }

  // Optional: mirror to COOJA.testlog
  // log.log("ID:" + id + " " + msg + "\n");
}
