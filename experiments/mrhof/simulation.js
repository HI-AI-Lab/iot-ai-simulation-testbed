// Run simulation for exactly this many milliseconds of simulated time
var Socket = Java.type("java.net.Socket");
var PrintWriter = Java.type("java.io.PrintWriter");
var InputStreamReader = Java.type("java.io.InputStreamReader");
var BufferedReader = Java.type("java.io.BufferedReader");

TIMEOUT(6000000, log.testOK()); // On timeout, exit with status 0

// --- One-shot ping at simulation start ---
try {
  var sock = new Socket("localhost", 5000);
  var out = new PrintWriter(sock.getOutputStream(), true);
  var inp = new BufferedReader(new InputStreamReader(sock.getInputStream()));

  var msg = "PING";
  log.log("JS sending: " + msg + "\n");
  out.println(msg);

  var line = inp.readLine();
  if (line != null) {
    log.log("JS got reply: " + line + "\n");
  }

  sock.close();
} catch (e) {
  log.log("Socket error: " + e + "\n");
}

while (true) {
  YIELD();
  // Append every mote output to the simulator log
  // Variables provided: time (ms), id (mote id), msg (string)
  log.log(time + "\t" + id + "\t" + msg + "\n");
}
