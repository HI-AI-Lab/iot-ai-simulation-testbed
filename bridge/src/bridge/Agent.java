package bridge;

/**
 * Stable, generic interface for Java<->Python via Py4J.
 * You can keep this forever and route new functionality by `method` name.
 * If you ever need more typed methods, add them here and recompile quickly.
 */
public interface Agent {
  /**
   * Generic RPC: JSON-in -> JSON-out
   * `method` is an arbitrary function name; `json` is the payload.
   * Return value must be a JSON string.
   */
  String rpc(String method, String json);

  /** Simple readiness/health probe. */
  boolean ping();
}
