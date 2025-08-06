log.writeFile("/workspace/mubasher.log", "Hi! This is a file write");
TIMEOUT(60000,log.testOK());
