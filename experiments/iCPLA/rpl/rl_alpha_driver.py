#!/usr/bin/env python3
"""
RL Alpha Driver / Orchestrator

- Builds app.cooja
- Launches Cooja headless
- Waits for SerialSocket (root mote) at 127.0.0.1:60001
- Connects and pushes ALPHA using Tabular Q-learning or DQN (if torch present)
- Tails log for metrics (PRR_ROOT, QLR, E2E) to compute reward
- Exits when Cooja ends
"""
import os, sys, time, socket, signal, subprocess, threading, re, random

# --- Paths (env overridable) ---
CONTIKI_NG = os.getenv("CONTIKI_NG", "/workspace/contiki-ng")
APP_DIR    = os.getenv("APP_DIR", "/workspace/testbed/experiments/iCPLA/rpl")
CSC        = os.getenv("CSC", f"{APP_DIR}/simulation.csc")
LOGFILE    = os.getenv("LOGFILE", "/workspace/COOJA.testlog")

HOST       = os.getenv("RL_HOST", "127.0.0.1")
PORT       = int(os.getenv("RL_PORT", "60001"))

# --- Alpha action grid ---
ALPHA_MIN  = float(os.getenv("RL_ALPHA_MIN", "0.05"))
ALPHA_MAX  = float(os.getenv("RL_ALPHA_MAX", "0.95"))
ALPHA_STEP = float(os.getenv("RL_ALPHA_STEP", "0.05"))
ACTIONS    = [round(ALPHA_MIN + i*ALPHA_STEP, 3)
              for i in range(int(round((ALPHA_MAX-ALPHA_MIN)/ALPHA_STEP))+1)]
ACTN       = len(ACTIONS)

INIT_ALPHA = float(os.getenv("RL_INIT_ALPHA", "0.30"))

# --- RL knobs ---
ALGO       = os.getenv("RL_ALGO", "qlearn").lower()  # 'qlearn' | 'dqn'
GAMMA      = float(os.getenv("RL_GAMMA", "0.90"))
LR         = float(os.getenv("RL_LR",    "0.20"))
EPS_START  = float(os.getenv("RL_EPS_START", "0.20"))
EPS_END    = float(os.getenv("RL_EPS_END",   "0.05"))
EPS_DECAY  = int(os.getenv("RL_EPS_DECAY_STEPS", "200"))

STEP_SEC   = float(os.getenv("RL_UPDATE_EVERY", "2.0"))
WARMUP_SEC = float(os.getenv("RL_WARMUP_SEC",   "5.0"))

# Reward weights
W_PRR      = float(os.getenv("RL_W_PRR", "1.0"))
W_QLR      = float(os.getenv("RL_W_QLR", "0.5"))
W_E2E      = float(os.getenv("RL_W_E2E", "0.5"))
E2E_SCALE  = float(os.getenv("RL_E2E_SCALE", "1000.0"))

_running = True
def _sig(_s, _f):
    global _running; _running = False
signal.signal(signal.SIGINT, _sig)
signal.signal(signal.SIGTERM, _sig)

def log(*a): print(time.strftime("[%H:%M:%S]"), *a, flush=True)

# ------------------ COOJA orchestration ------------------
def build():
    log("building app.cooja …")
    p = subprocess.run(["make", "-C", APP_DIR, "TARGET=cooja", "app.cooja"])
    if p.returncode != 0:
        raise SystemExit("make failed")

def start_cooja():
    log("starting cooja …")
    gradle = f"{CONTIKI_NG}/tools/cooja/gradlew"
    proj   = f"{CONTIKI_NG}/tools/cooja"
    args   = f"--no-gui {CSC} --contiki={CONTIKI_NG} --logfile={LOGFILE}"
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

# ------------------ Metrics tailer ------------------
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
            with self.lock: self.prr_root = float(prr.group(1)); return
        qlr = METRIC_P_QLR.search(line)
        if qlr:
            with self.lock: self.qlr_vals[time.time()] = float(qlr.group(1)); return
        e2e = METRIC_P_E2E.search(line)
        if e2e:
            with self.lock: self.e2e_last.append(float(e2e.group(1))); return

    def snapshot(self):
        with self.lock:
            prr = self.prr_root
            qlr = (sum(self.qlr_vals.values())/len(self.qlr_vals)) if self.qlr_vals else None
            e2e = (sum(self.e2e_last)/len(self.e2e_last)) if self.e2e_last else None
        return prr, qlr, e2e

def tail_log(path, metrics: Metrics, cooja_proc):
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
                    if cooja_proc.poll() is not None: return
                    time.sleep(0.05); f.seek(where)
                else:
                    metrics.ingest_line(line)
    except Exception as e:
        log("[tail]", "stopped:", e)

# ------------------ RL ------------------
def reward_fn(prr, qlr, e2e):
    prr_v = 0.0 if prr is None else max(0.0, min(1.0, prr))
    qlr_v = 1.0 if qlr is None else max(0.0, min(1.0, qlr))
    e2e_v = 1.0 if e2e is None else max(0.0, min(2.0, e2e / E2E_SCALE))
    return (W_PRR * prr_v) - (W_QLR * qlr_v) - (W_E2E * e2e_v)

def run_qlearn(sock, metrics, cooja_proc):
    from collections import defaultdict
    Q = defaultdict(lambda: [0.0]*ACTN)
    step = 0
    eps  = EPS_START

    def make_state(prr, qlr, e2e):
        def b(x): return 10 if x is None else max(0, min(10, int(x*10 + 1e-9)))
        def be(ms): return 10 if ms is None else max(0, min(10, int(ms/100.0 + 1e-9)))
        return (b(prr), b(qlr), be(e2e))

    alpha = send_alpha(sock, INIT_ALPHA)
    a_idx = min(range(ACTN), key=lambda i: abs(ACTIONS[i]-alpha))
    time.sleep(WARMUP_SEC)
    prr, qlr, e2e = metrics.snapshot()
    s = make_state(prr, qlr, e2e)
    last_push = time.time()

    while _running:
        if cooja_proc.poll() is not None:
            log("cooja finished; RL exiting"); break
        now = time.time()
        if now - last_push < STEP_SEC:
            time.sleep(0.05); continue

        prr, qlr, e2e = metrics.snapshot()
        r = reward_fn(prr, qlr, e2e)
        sp = make_state(prr, qlr, e2e)

        # decay eps
        if EPS_DECAY > 0:
            eps = max(EPS_END, EPS_START - (EPS_START - EPS_END) * min(1.0, step / EPS_DECAY))
        else:
            eps = EPS_END

        # Q update
        qsa = Q[s][a_idx]
        max_sp = max(Q[sp])
        Q[s][a_idx] = qsa + LR * (r + GAMMA * max_sp - qsa)

        # action select
        import random as _r
        if _r.random() < eps:
            a_next = _r.randrange(ACTN)
        else:
            m = max(Q[sp]); idxs = [i for i,v in enumerate(Q[sp]) if v == m]; a_next = _r.choice(idxs)

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
            log("socket err:", e); break

def run_dqn(sock, metrics, cooja_proc):
    try:
        import torch, torch.nn as nn, torch.optim as optim
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        try: torch.set_float32_matmul_precision("medium")
        except Exception: pass
    except Exception as e:
        log("[dqn] torch not found:", e, "-> falling back to qlearn")
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

    def make_state(prr, qlr, e2e):
        def b(x): return 10 if x is None else max(0, min(10, int(x*10 + 1e-9)))
        def be(ms): return 10 if ms is None else max(0, min(10, int(ms/100.0 + 1e-9)))
        return (b(prr), b(qlr), be(e2e))

    # init
    alpha = send_alpha(sock, INIT_ALPHA)
    a_idx = min(range(ACTN), key=lambda i: abs(ACTIONS[i]-alpha))
    time.sleep(WARMUP_SEC)
    prr, qlr, e2e = metrics.snapshot()
    s = make_state(prr, qlr, e2e)
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
        r = reward_fn(prr, qlr, e2e)
        sp = make_state(prr, qlr, e2e)

        with torch.no_grad():
            q_next = net(st(sp)).max()
            q_tgt  = r + GAMMA * q_next

        q_pred = net(st(s))[0, a_idx]
        loss = loss_f(q_pred, q_tgt)
        opt.zero_grad(); loss.backward(); opt.step()

        # eps schedule
        if EPS_DECAY > 0:
            eps = max(EPS_END, EPS_START - (EPS_START - EPS_END) * min(1.0, step / EPS_DECAY))
        else:
            eps = EPS_END

        # select next action
        if random.random() < eps:
            a_next = random.randrange(ACTN)
        else:
            with torch.no_grad():
                a_next = int(net(st(sp)).argmax().item())

        if a_next != a_idx or (now - last_push) >= max(STEP_SEC, 2.0):
            alpha = send_alpha(sock, ACTIONS[a_next]); last_push = time.time()
        a_idx = a_next; s = sp; step += 1

        # health
        try:
            b = sock.recv(1)
            if b == b"": log("server closed socket; RL exiting"); break
        except socket.timeout:
            pass
        except OSError as e:
            log("socket err:", e); break

# ------------------ main ------------------
def main():
    build()
    cooja = start_cooja()
    try:
        sock = wait_for_port(HOST, PORT, cooja_proc=cooja)
        metrics = Metrics()
        t = threading.Thread(target=tail_log, args=(LOGFILE, metrics, cooja), daemon=True)
        t.start()

        if ALGO == "dqn":
            run_dqn(sock, metrics, cooja)
        else:
            run_qlearn(sock, metrics, cooja)

        rc = cooja.wait()
        log("cooja exit:", rc)
    finally:
        try:
            if cooja.poll() is None:
                cooja.terminate(); cooja.wait(timeout=5)
        except Exception:
            try: cooja.kill()
            except Exception: pass

if __name__ == "__main__":
    main()
