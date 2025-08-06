TIMEOUT(60000), log.testOK()); // Schedule a stop at 60 seconds
while (time < 60000) {
  YIELD();
  if(msg){
    log.log("mote output: " + msg + "\n");
  }
}
