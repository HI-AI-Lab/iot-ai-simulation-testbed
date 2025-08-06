TIMEOUT(60000, log.testOK());
while (time < 60000) {
  YIELD();
  log.log("Time: " + time + "\n");
}
