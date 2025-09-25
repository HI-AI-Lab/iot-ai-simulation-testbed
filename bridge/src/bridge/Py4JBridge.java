package bridge;

import py4j.GatewayServer;

/**
 * Entry point hosted in the JVM (COOJA). Your controller JS will:
 *   - new Py4JBridge()
 *   - start(port)
 *   - spawn Python (py_agent.py) which connects and calls setAgent(...)
 *   - poll isReady()
 *   - call rpc("method", json)
 */
public class Py4JBridge {
  private volatile Agent agent;
  private GatewayServer server;

  /** Start a Java-side GatewayServer that Python connects to. Safe to call twice. */
  public synchronized int start(int port) {
    if (server == null) {
      server = new GatewayServer(this, port);
      server.start();
      System.out.println("[Py4JBridge] Gateway started on port " + server.getListeningPort());
    }
    return server.getListeningPort();
  }

  /** Called by Python once it has connected to the gateway. */
  public void setAgent(Agent a) {
    this.agent = a;
    System.out.println("[Py4JBridge] Agent registered: " + (a != null));
  }

  /** Ready when Python has registered its Agent implementation. */
  public boolean isReady() {
    return agent != null && agent.ping();
  }

  /**
   * Generic RPC into Python.
   * Pass an arbitrary method name and a JSON payload; receive a JSON string back.
   */
  public String rpc(String method, String json) {
    Agent a = this.agent;
    if (a == null) throw new IllegalStateException("Agent not set (Python not registered yet)");
    return a.rpc(method, json);
  }

  /** Stop the Gateway (optional). */
  public synchronized void stop() {
    if (server != null) {
      server.shutdown();
      server = null;
      System.out.println("[Py4JBridge] Gateway stopped");
    }
    agent = null;
  }
}
