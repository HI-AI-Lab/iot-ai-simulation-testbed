package io.testbed.rl;

/**
 * Minimal stub Agent to test JVM <-> ScriptRunner integration.
 * Later we will replace internals with RL logic.
 */
public class Agent {

    public Agent(int k, int F) {
        // accept same constructor signature for forward compatibility
        System.out.println("Agent initialized with k=" + k + " F=" + F);
    }

    /** 
     * Test function: returns input + 1 
     */
    public int increment(int x) {
        return x + 1;
    }
}
