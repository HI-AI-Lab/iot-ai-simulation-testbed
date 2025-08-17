var RUN_DURATION_MS = 300000;

TIMEOUT(RUN_DURATION_MS, log.testOK());

while (true) {
  YIELD();
  log.log(time + "\t" + id + "\t" + msg + "\n");
}
