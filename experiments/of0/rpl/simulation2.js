log.log("Simulation started\n");
TIMEOUT(60000);
YIELD_THEN_WAIT_UNTIL(false); // This will trigger TIMEOUT
log.log("Timeout: stopping.\n");
sim.stop();
