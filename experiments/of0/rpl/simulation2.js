log.log("Simulation started\n");
TIMEOUT(60000);
YIELD(); // Wait until TIMEOUT is triggered
log.log("Simulation reached 60 seconds. Ending.\n");
log.testOK();
