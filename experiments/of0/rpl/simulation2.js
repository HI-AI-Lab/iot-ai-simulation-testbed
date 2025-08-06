log.log("Simulation started\n");

// Optional: log when simulation time is up
TIMEOUT(60000);

while (time < 60000) {
  YIELD(); // Keeps control in simulation until 60 seconds
}

log.log("Simulation reached 60 seconds. Ending.\n");
log.testOK(); // Must be called before simulation stops
