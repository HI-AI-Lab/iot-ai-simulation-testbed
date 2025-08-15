// Run simulation for exactly this many milliseconds of simulated time
var run_duration_ms = 300000; // 5 minutes

TIMEOUT(300000, log.testOK()); // On timeout, exit with status 0

while (true) {
  YIELD();
  // Append every mote output to the simulator log
  // Variables provided: time (ms), id (mote id), msg (string)
  log.log(time + "\t" + id + "\t" + msg + "\n");
}
