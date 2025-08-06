log.log("Simulation started\n");

GENERATE_MSG(60000, "timeout");

YIELD_THEN_WAIT_UNTIL(msg.equals("timeout"));

log.log("Timeout reached. Stopping simulation.\n");

log.testOK(); // Report test success and quit
