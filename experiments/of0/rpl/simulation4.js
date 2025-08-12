// Run for this many milliseconds of simulation time
var DURATION_MS = 60000; // 60 seconds

// Stop after timeout (expression only, no `throw`)
TIMEOUT(DURATION_MS, (log.log("Capture finished after " + DURATION_MS + " ms\n"), log.testOK()));

// Inform start
log.log("Logging all mote output...\n");

// Loop forever, capturing mote output
while (true) {
  YIELD(); // Wait for mote output (gives you id, time, msg)
  log.log("ID:" + id + "  TIME:" + time + "  MSG:" + msg + "\n");
}
