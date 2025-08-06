log.log("Simulation started\n");
TIMEOUT(60000); // Schedule a stop at 60 seconds
while (time < 60000) {
  YIELD();
}
log.log("Simulation ended at: " + time + "\n");
log.testOK();
