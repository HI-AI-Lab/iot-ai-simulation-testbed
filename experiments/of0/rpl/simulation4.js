// Run for this many milliseconds of simulation time
var DURATION_MS = 60000; // 60 seconds

// On timeout: just mark success (expression only!)
TIMEOUT(DURATION_MS, log.testOK());

// Optional: note start in COOJA.testlog
//log.log("Logging all mote output...\n");

// Capture every mote line exactly as COOJA provides it.
// 'id' = mote id, 'time' = sim time (ms), 'msg' = raw string shown in GUI.
//while (true) {
//  YIELD(); // wait for next mote output
//  log.log("ID:" + id + "  TIME:" + time + "  MSG:" + msg + "\n");
//}
