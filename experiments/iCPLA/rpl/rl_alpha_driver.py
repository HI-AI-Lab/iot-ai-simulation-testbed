#!/usr/bin/env python3
import socket
import time
import random
import re
import sys

HOST = "127.0.0.1"  # SerialSocketServer host
PORT = 60001        # must match the <port> in your .csc plugin

# Simple stateful RL placeholder:
# - tracks recent PRR/QLR from logs (optional: tail a file)
# - adapts alpha in [0.05..0.95]
ALPHA_MIN = 0.05
ALPHA_MAX = 0.95
STEP_UP   = 0.05
STEP_DOWN = 0.05

def send_alpha(sock, a):
    a = max(ALPHA_MIN, min(ALPHA_MAX, a))
    line = f"ALPHA={a:.3f}\n".encode()
    sock.sendall(line)
    print(time.strftime("[%H:%M:%S]"), "push", line.decode().strip(), flush=True)

def main():
    # Connect to Cooja SerialSocketServer (root mote)
    try:
        sock = socket.create_connection((HOST, PORT))
    except OSError as e:
        print("ERROR: cannot connect to SerialSocketServer:", e)
        sys.exit(1)

    # Toy policy: start mid, then adapt randomly a bit
    alpha = 0.30
    send_alpha(sock, alpha)

    try:
        while True:
            # ---- replace this section with your real RL logic ----
            # For now: random walk with slight bias to explore
            delta = random.choice([-STEP_DOWN, 0.0, STEP_UP])
            alpha = max(ALPHA_MIN, min(ALPHA_MAX, alpha + delta))
            send_alpha(sock, alpha)
            time.sleep(15)
            # ------------------------------------------------------
    except KeyboardInterrupt:
        pass
    finally:
        sock.close()

if __name__ == "__main__":
    main()
