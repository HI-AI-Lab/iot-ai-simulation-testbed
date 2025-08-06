TIMEOUT(60000, log.testOK());
while (true) {
  YIELD();

  if (msg) {
    log.log(msg + "\n");  // This prints all printf/LOG_INFO etc. from motes
  }
}
