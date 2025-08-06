log.log("Simulation started\n");

function timeoutHandler() {
  log.log("Timeout: stopping.\n");
  sim.stop();
}

TIMEOUT(60000, timeoutHandler);

YIELD(); // Wait for any output
log.log("First output: " + msg + "\n");

log.testOK(); // Mark the test as successful
