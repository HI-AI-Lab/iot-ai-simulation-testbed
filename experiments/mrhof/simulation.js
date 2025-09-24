// Run simulation for exactly this many milliseconds of simulated time
var Socket = Java.type("java.net.Socket");
var PrintWriter = Java.type("java.io.PrintWriter");
var InputStreamReader = Java.type("java.io.InputStreamReader");
var BufferedReader = Java.type("java.io.BufferedReader");

TIMEOUT(6000000, log.testOK()); // On timeout, exit with status 0

// --- Open the socket once, before the main loop ---
try {
    //var sock = new Socket("localhost", 5000);
    //var out = new PrintWriter(sock.getOutputStream(), true);
    //var inp = new BufferedReader(new InputStreamReader(sock.getInputStream()));

    // --- Main simulation loop ---
    while (true) {
        YIELD();
		
        // Variables provided: time (ms), id (mote id), msg (string)        
        // Check if the message is intended for the AI agent
        //if (msg.startsWith("[INFO: App       ] TOGGLE:")) {
			//log.log(time + "\t" + id + "\t" + "Talking to AI Agent: " + msg + "\n");
            //out.println(msg);
			// var mem = motes[id-1].getMemory();
			// mem.setIntValue("toggle_value", 1);
			//out.println(time + "\t" + id + "\t" + "Set a value in ai_value\n");
            // You can optionally read a reply here
            // var line = inp.readLine();
            // if (line != null) {
            //     log.log("JS got reply: " + line + "\n");
            // }
        //} else {
            // Log all other messages to the simulator
            log.log(time + "\t" + id + "\t" + msg + "\n");
        //}
    }
    //sock.close(); // This will only be reached if the loop terminates
} catch (e) {
    log.log("Socket error: " + e + "\n");
}