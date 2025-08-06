TIMEOUT(60000); // Schedule a stop at 60 seconds
while (time < 60000) {
  YIELD();
}
log.testOK();
