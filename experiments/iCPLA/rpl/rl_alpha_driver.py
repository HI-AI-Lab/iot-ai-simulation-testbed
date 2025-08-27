#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Build + run COOJA headless and control iCPLA alpha (α) with real RL.

- Tabular Q-learning by default (discretized state).
- DQN (tiny MLP) if RL_ALGO=dqn; runs on CUDA if available.
- Tails COOJA.testlog for PRR/QLR/E2E to compute reward.
- Connects to SerialSocket (SERVER) on root (default 127.0.0.1:60001).
- Exits automatically when the simulation ends.

ENV overrides (sensible defaults):
  CONTIKI_NG=/workspace/contiki-ng
  APP_DIR=/workspace/testbed/experiments/iCPLA/rpl
  CSC=$APP_DIR/simulation.csc
  LOGFILE=/workspace/COOJA.testlog
  RL_HOST=127.0.0.1 RL_PORT=60001
  RL_ALGO=qlearn|dqn  RL_INIT_ALPHA=0.30 RL_UPDATE_EVERY=2.0 RL_WARMUP_SEC=5.0
  RL_ALPHA_MIN=0.05 RL_ALPHA_MAX=0.95 RL_ALPHA_STEP=0.05
  RL_GAMMA=0.90 RL_LR=0.20 RL_EPS_START=0.20 RL_EPS_END=0.05 RL_EPS_DECAY_STEPS=200
  RL_W_PRR=1.0 RL_W_QLR=0.5 RL_W_E2E=0.5 RL_E2E_SCALE=1000.0
  MIRROR_LOG_TO_STDOUT=0|1
"""
import os, sys, time, socket, signal, subprocess, threading, re, random

# --------------------------- Config ---------------------------
CONTIKI_NG = os.getenv("CONTIKI_NG", "/workspace/contiki-ng")
APP_DIR    = os.getenv("APP_DIR", "/workspace/testbed/experiments/iCPLA/rpl")
CSC        = os.getenv("CSC", f"{APP_DIR}/simulation.csc")
LOGFILE    = os.getenv("LOGFILE", "/workspace/COOJA.testlog")

HOST       = os.getenv("RL_HOST", "127.0.0.1")
PORT       = int(os.getenv("RL_PORT", "60001"))

ALPHA_MIN  = float(os.getenv("RL_ALPHA_MIN", "0.05"))
ALPHA_MAX  = float(os.getenv("RL_ALPHA_MAX", "0.95"))
ALPHA_STEP = float(os.getenv("RL_ALPHA_STEP", "0.05"))
ACTIONS    = [round(ALPHA_MIN + i*ALPHA_STEP, 3)
              for i in range(int(round((ALPHA_MAX-ALPHA_MIN)/ALPHA_STEP))+1)]
ACTN       = len(ACTIONS)
INIT_ALPHA = float(os.getenv("RL_INIT_ALPHA", "0.30"))

ALGO       = os.getenv("RL_ALGO", "qlearn").lower()     # 'qlearn' | 'dqn'
GAMMA      = float(os.getenv("RL_GAMMA", "0.90"))
LR         = float(os.getenv("RL_LR",    "0.20"))
EPS_START  = float(os.getenv("RL_EPS_START", "0.20"))
EPS_END    = float(os.getenv("RL_EPS_END",   "0.05"))
EPS_DECAY  = int(os.getenv("RL_EPS_DECAY_STEPS", "200"))

STEP_SEC   = float(os.getenv("RL_UPDATE_EVERY", "2.0"))
WARMUP_SEC = float(os.getenv("RL_WARMUP_SEC",   "5.0"))

W_PRR      = float(os.getenv("RL_W_PRR", "1.0"))
W_QLR      = float(os.getenv("RL_W_QLR", "0.5"))
W_E2E      = float(os.getenv("RL_W_E2E", "0.5"))
E2E_SCALE  = float(os.getenv("RL_E2E_SCALE", "1000.0"))

MIRROR     = os.getenv("MIRROR_LOG_TO_STDOUT", "0") == "1"

# --------------------------- Utils ---------------------------
_running = True
def _sig(_s, _f):
    global _running; _running = False
signal.signal(signal.SIGINT, _sig)
signal.signal(signal.SIGTERM, _sig)

def log(*a): print(time.strftime("[%H:%M:%S]"), *a, flush=True)

def build():
    log("building app.cooja …")
    p = subprocess.run(["make", "-C", APP_DIR, "TARGET=cooja", "app.cooja"])
    if p.returncode != 0:
        raise SystemExit("make failed")

def start_cooja():
    """
    Start COOJA headless. Do NOT pass --logfile (newer COOJA ignores it);
    COOJA will itself write to LOGFILE (e.g., /workspace/COOJA.testlog)
    as configured by the simulation's Script/Log settings.
    """
    log("starting cooja headless …")
    gradle = f"{CONTIKI_NG}/tools/cooja/gradlew"
    proj   = f"{CONTIKI_NG}/tools/cooja"
    args   = f"--no-gui {CSC} --contiki={CONTIKI_NG}"
    # Let Gradle output go to our stdout; simulation logs go into LOGFILE.
    return subprocess.Popen([gradle, "-p", proj, "run", f"--args={args}"])

def wait_for_port(host, port, cooja_proc=None, backoff=0.2):
    log(f"waiting for {host}:{port} …")
    while _running:
        if cooja_proc is not None and cooja_proc.poll() is not None:
            raise SystemExit("cooja ended before SerialSocket opened")
        try:
            s = socket.create_connection((host, port), timeout=2.0)
            s.settimeout(2.0)
            log(f"connected to {host}:{port}")
            return s
        except OSError:
            time.sleep(backoff)
    raise SystemExit(0)

def send_alpha(sock, a):
    a = max(ALPHA_MIN, min(ALPHA_MAX, a))
    line = f"ALPHA={a:.3f}\n".encode()
    sock.sendall(line)
    log("push", line.decode().strip())
    return a

# --------------------------- Metrics ---------------------------
METRIC_P_PRR_ROOT = re.compile(r"METRIC\s+PRR_ROOT.*?\bprr=([0-9.]+)")
METRIC_P_QLR      = re.compile(r"METRIC\s+QLR\b.*?\bqlr=([0-9.]+)")
METRIC_P_E2E      = re.compile(r"METRIC\s+E2E\b.*?\be2e_ms=([0-9.]+)")

class Metrics:
    def __init__(self):
        from collections import deque
        import threading
        self.lock = threading.Lock()
        self.prr_root = None
        self.qlr_vals = {}
        self.e2e_last = deque(maxlen=200)

    def ingest_line(self, line: str):
        prr = METRIC_P_PRR_ROOT.search(line)
        if prr:
            with self.lock:
                self.prr_root = float(prr.group(1))
            return
        qlr = METRIC_P_QLR.search(line)
        if qlr:
            with self.lock:
                self.qlr_vals[time.time()] = float(qlr.group(1))
            return
        e2e = METRIC_P_E2E.search(line)
        if e2e:
            with self.lock:
                self.e2e_last.append(float(e2e.group(1)))
            return

    def snapshot(self):
        with self.lock:
            prr = self.prr_root
            qlr = (sum(self.qlr_vals.values())/len(self.qlr_vals)) if self.qlr_vals else None
            e2e = (sum(self.e2e_last)/len(self.e2e_last)) if self.e2e_last else None
        return prr, qlr, e2e

def tail_log(path, metrics: Metrics, cooja_proc):
    # wait for logfile to appear
    while _running and not os.path.exists(path):
        if cooja_proc.poll() is not None: return
        time.sleep(0.1)
    try:
        with open(path, "r", errors="ignore") as f:
            f.seek(0, os.SEEK_END)
            while _running:
                where = f.tell()
                line  = f.readline()
                if not line:
                    if cooja_proc.poll() is not None:
                        return
                    time.sleep(0.05)
                    f.seek(where)
                else:
                    metrics.ingest_line(line)
                    if MIRROR:
                        sys.stdout.write(line); sys.stdout.flush()
    except Exception as e:
        log("[tail] stopped:", e)

# --------------------------- RL ---------------------------
def reward_fn(prr, qlr, e2e):
    """Higher PRR better; lower QLR/E2E better; normalize into ~[0,1]."""
    prr_v = 0.0 if prr is None else max(0.0, min(1.0, prr))
    qlr_v = 1.0 if qlr is None else max(0.0, min(1.0, qlr))
    e2e_v = 1.0 if e2e is None else max(0.0, min(2.0, e2e / E2E_SCALE))
    return (W_PRR * prr_v) - (W_QLR * qlr_v) - (W_E2E * e2e_v)

def make_state_bins(prr, qlr, e2e):
    def b01(x):  # 0..1 -> 0..10 (None->10 worst)
        return 10 if x is None else max(0, min(10, int(x*10 + 1e-9)))
    def be(ms):  # 0..∞ -> 0..10 by ~100ms buckets (None->10)
        return 10 if ms is None else max(0, min(10, int(ms/100.0 + 1e-9)))
    return (b01(prr), b01(qlr), be(e2e))

def run_qlearn(sock, metrics, cooja_proc):
    from collections import defaultdict
    Q = defaultdict(lambda: [0.0]*ACTN)
    step = 0
    eps  = EPS_START

    alpha = send_alpha(sock, INIT_ALPHA)
    a_idx = min(range(ACTN), key=lambda i: abs(ACTIONS[i]-alpha))

    time.sleep(WARMUP_SEC)
    prr, qlr, e2e = metrics.snapshot()
    s = make_state_bins(prr, qlr, e2e)
    last_push = time.time()

    while _running:
        if cooja_proc.poll() is not None:
            log("cooja finished; RL exiting"); break

        now = time.time()
        if now - last_push < STEP_SEC:
            time.sleep(0.05); continue

        prr, qlr, e2e = metrics.snapshot()
        r  = reward_fn(prr, qlr, e2e)
        sp = make_state_bins(prr, qlr, e2e)

        # Q-learning update
        qsa = Q[s][a_idx]
        max_sp = max(Q[sp])
        Q[s][a_idx] = qsa + LR * (r + GAMMA * max_sp - qsa)

        # ε-greedy selection
        if EPS_DECAY > 0:
            eps = max(EPS_END, EPS_START - (EPS_START - EPS_END) * min(1.0, step / EPS_DECAY))
        else:
            eps = EPS_END
        if random.random() < eps:
            a_next = random.randrange(ACTN)
        else:
            m = max(Q[sp]); idxs = [i for i,v in enumerate(Q[sp]) if v == m]; a_next = random.choice(idxs)

        if a_next != a_idx or (now - last_push) >= max(STEP_SEC, 2.0):
            alpha = send_alpha(sock, ACTIONS[a_next]); last_push = time.time()
        a_idx = a_next; s = sp; step += 1

        # health check
        try:
            b = sock.recv(1)
            if b == b"": log("server closed socket; RL exiting"); break
        except socket.timeout:
            pass
        except OSError as e:
            log("socket recv error:", e); break

def run_dqn(sock, metrics, cooja_proc):
    try:
        import torch, torch.nn as nn, torch.optim as optim
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        try: torch.set_float32_matmul_precision("medium")
        except Exception: pass
    except Exception as e:
        log("[dqn] torch unavailable:", e, "-> falling back to qlearn")
        return run_qlearn(sock, metrics, cooja_proc)

    class Net(nn.Module):
        def __init__(self):
            super().__init__()
            self.m = nn.Sequential(
                nn.Linear(3, 64), nn.ReLU(),
                nn.Linear(64, 64), nn.ReLU(),
                nn.Linear(64, ACTN)
            )
        def forward(self, x): return self.m(x)

    net = Net().to(device)
    opt = optim.Adam(net.parameters(), lr=LR)
    loss_f = nn.SmoothL1Loss()

    def st(s):
        import torch
        x = torch.tensor([s[0]/10.0, s[1]/10.0, s[2]/10.0], dtype=torch.float32, device=device)
        return x.unsqueeze(0)

    alpha = send_alpha(sock, INIT_ALPHA)
    a_idx = min(range(ACTN), key=lambda i: abs(ACTIONS[i]-alpha))

    time.sleep(WARMUP_SEC)
    prr, qlr, e2e = metrics.snapshot()
    s   = make_state_bins(prr, qlr, e2e)
    eps = EPS_START
    step = 0
    last_push = time.time()

    while _running:
        if cooja_proc.poll() is not None:
            log("cooja finished; RL exiting"); break

        now = time.time()
        if now - last_push < STEP_SEC:
            time.sleep(0.05); continue

        prr, qlr, e2e = metrics.snapshot()
        r  = reward_fn(prr, qlr, e2e)
        sp = make_state_bins(prr, qlr, e2e)

        # DQN update (bootstrap target, no target net for simplicity)
        import torch
        with torch.no_grad():
            q_next = net(st(sp)).max()
            q_tgt  = r + GAMMA * q_next
        q_pred = net(st(s))[0, a_idx]
        loss = loss_f(q_pred, q_tgt)
        opt.zero_grad(); loss.backward(); opt.step()

        # ε schedule
        if EPS_DECAY > 0:
            eps = max(EPS_END, EPS_START - (EPS_START - EPS_END) * min(1.0, step / EPS_DECAY))
        else:
            eps = EPS_END

        # action selection
        if random.random() < eps:
            a_next = random.randrange(ACTN)
        else:
            with torch.no_grad():
                a_next = int(net(st(sp)).argmax().item())

        if a_next != a_idx or (now - last_push) >= max(STEP_SEC, 2.0):
            alpha = send_alpha(sock, ACTIONS[a_next]); last_push = time.time()
        a_idx = a_next; s = sp; step += 1

        # health check
        try:
            b = sock.recv(1)
            if b == b"": log("server closed socket; RL exiting"); break
        except socket.timeout:
            pass
        except OSError as e:
            log("socket recv error:", e); break

# --------------------------- Main ---------------------------
def main():
    build()
    cooja = start_cooja()
    try:
        # tail metrics and (optionally) mirror to stdout
        metrics = Metrics()
        threading.Thread(target=tail_log, args=(LOGFILE, metrics, cooja), daemon=True).start()

        # wait for SerialSocket server
        sock = wait_for_port(HOST, PORT, cooja_proc=cooja)

        # run RL
        if ALGO == "dqn":
            run_dqn(sock, metrics, cooja)
        else:
            run_qlearn(sock, metrics, cooja)

        rc = cooja.wait()
        log("cooja exit code:", rc)
    finally:
        try:
            if cooja.poll() is None:
                cooja.terminate(); cooja.wait(timeout=5)
        except Exception:
            try: cooja.kill()
            except Exception: pass

if __name__ == "__main__":
    main()
