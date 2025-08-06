TIMEOUT(60000);

log.log("Simulation started\n");

while (time < 60000) {
  YIELD();
  log.log("MOTE " + id + " @ " + time + "ms: " + msg + "\n");
}

log.log("Simulation finished at: " + time + "ms\n");
log.testOK();
