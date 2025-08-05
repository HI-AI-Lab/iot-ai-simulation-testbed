log.log("Simulation started\n");
TIMEOUT(60000, function() {
  log.log("Timeout: stopping.\n");
  sim.stop();
});
