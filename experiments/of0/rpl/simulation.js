// simulation.js
var RUN_DURATION_MS = 300000; // 5 minutes

// End the test after RUN_DURATION_MS and mark it as success
TIMEOUT(RUN_DURATION_MS, log.testOK());

// Main loop: print all mote output to the COOJA log
while (true) {
  YIELD(); // wait for a new mote message
  log.log(time + "\t" + id + "\t" + msg + "\n");
}
