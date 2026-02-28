#!/usr/bin/env python3
"""
python3 run_parallel.py \
  --ararl-dir /workspace/testbed/experiments/ararl \
  --logs-dir  /workspace/testbed/logs \
  --gradle-root /workspace/contiki-ng/tools/cooja \
  --nodes 60 80 100 \
  --ppm 80 100 120 \
  --topologies 10 \
  --mask-file /workspace/testbed/mask.yaml \
  --mask-name baseline \
  --jobs 0 \
  --work-root /workspace/testbed/_work
"""
from __future__ import annotations

import argparse, csv, json, os, re, shutil, statistics, subprocess, sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed
import time, selectors

try:
    import yaml  # optional, for nicer meta YAML
except Exception:
    yaml = None

def backup_if_exists(p: Path) -> None:
    if not p.exists():
        return
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = p.with_name(p.name + f".bak_{ts}")
    p.rename(bak)

def auto_mask_name_from_file(mask_path: Path) -> str:
    enabled = read_mask_enabled(mask_path)

    if enabled == ["ALL"]:
        return "all"
    if not enabled:
        return "none"

    # stable and readable folder name
    name = "_".join(enabled)
    name = re.sub(r"[^A-Za-z0-9_-]+", "_", name).strip("_")
    return name or "mask"

def read_mask_enabled(mask_path: Path) -> List[str]:
    if yaml is not None:
        d = yaml.safe_load(mask_path.read_text(encoding="utf-8"))
        feats = (d or {}).get("features", {}) or {}
        if feats.get("all", False):
            return ["ALL"]
        return sorted([k for k, v in feats.items() if k != "all" and bool(v)])

    # fallback parser
    enabled = []
    for line in mask_path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        k, v = [x.strip() for x in line.split(":", 1)]
        if k == "all":
            continue
        if v.lower() == "true":
            enabled.append(k)
    return sorted(enabled)

# ------------------------------ Config ------------------------------ #

@dataclass
class RunnerConfig:
    ararl_dir: Path
    logs_dir: Path
    gradle_root: Path
    nodes: List[int]
    ppms: List[int]
    topology_ids: List[str]
    mask_name: str
    mask_file: Path
    duration_sf: int
    warmup_sf: int
    sim_seed: int
    agent_seed: int
    traffic_seeds: List[int]
    tx_range: Optional[float]
    int_range: Optional[float]
    gradle_user_home: Optional[Path]
    jobs: int
    work_root: Path
    keep_work: bool
    dry_run: bool
    error_log_tail: int

# ------------------------------ Helpers ------------------------------ #

def die(msg: str, code: int = 2) -> None:
    print(f"[FATAL] {msg}", file=sys.stderr); sys.exit(code)

def sh(
    cmd: List[str],
    cwd: Optional[Path] = None,
    env: Optional[Dict[str, str]] = None,
    out_path: Optional[Path] = None,
    tag: str = "",
    heartbeat_secs: int = 60
) -> int:
    print(f"[RUN] {' '.join(cmd)} (cwd={cwd})")
    try:
        if out_path is None:
            proc = subprocess.Popen(cmd, cwd=str(cwd) if cwd else None, env=env)
            return proc.wait()

        ensure_dir(out_path.parent)

        start = time.time()
        last_print = start
        last_out = start
        last_line = ""

        proc = subprocess.Popen(
            cmd,
            cwd=str(cwd) if cwd else None,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1
        )

        assert proc.stdout is not None
        sel = selectors.DefaultSelector()
        sel.register(proc.stdout, selectors.EVENT_READ)

        with out_path.open("w", encoding="utf-8") as f:
            while True:
                # Read available output (non-blocking-ish)
                events = sel.select(timeout=1)
                for key, _ in events:
                    line = key.fileobj.readline()
                    if line:
                        f.write(line)
                        f.flush()
                        last_out = time.time()
                        s = line.strip()
                        if s:
                            last_line = s

                # Heartbeat (even if no output)
                now = time.time()
                if now - last_print >= heartbeat_secs:
                    elapsed = int(now - start)
                    mm, ss = divmod(elapsed, 60)
                    since = int(now - last_out)
                    size = out_path.stat().st_size if out_path.exists() else 0
                    snippet = (last_line[:120] + "…") if len(last_line) > 120 else last_line
                    print(f"[LIVE] {tag} +{mm:02d}:{ss:02d} last_out={since}s log={size/1024:.1f}KB last='{snippet}'")
                    last_print = now

                # Exit condition
                if proc.poll() is not None:
                    # drain any remaining output
                    while True:
                        line = proc.stdout.readline()
                        if not line:
                            break
                        f.write(line)
                        f.flush()
                    break

        return proc.wait()

    except FileNotFoundError:
        return 127

def ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)

def write_text(p: Path, s: str) -> None:
    ensure_dir(p.parent); p.write_text(s, encoding="utf-8")

def tail_lines(p: Path, n: int = 40) -> List[str]:
    if not p.is_file():
        return []
    try:
        lines = p.read_text(encoding="utf-8", errors="ignore").splitlines()
        if n <= 0:
            return lines
        return lines[-n:]
    except Exception:
        return []

def yaml_dump(d: Dict) -> str:
    if yaml is None:
        return "\n".join(f"{k}: {v}" for k, v in d.items())
    return yaml.safe_dump(d, sort_keys=False, allow_unicode=True)

def now_stamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

# ------------------------------ CPU / jobs auto-detection ------------------------------ #

def _affinity_cores() -> Optional[int]:
    try:
        return len(os.sched_getaffinity(0))  # respects taskset / cpuset
    except Exception:
        return None

def _cgroup_quota_cores() -> Optional[int]:
    try:
        # cgroup v2
        p = Path("/sys/fs/cgroup/cpu.max")
        if p.exists():
            quota, period = p.read_text().strip().split()
            if quota != "max":
                return max(1, int(float(quota) / float(period)))
        # cgroup v1
        q1 = Path("/sys/fs/cgroup/cpu/cpu.cfs_quota_us")
        p1 = Path("/sys/fs/cgroup/cpu/cpu.cfs_period_us")
        if q1.exists() and p1.exists():
            q = int(q1.read_text().strip()); per = int(p1.read_text().strip())
            if q > 0 and per > 0:
                return max(1, int(q / per))
    except Exception:
        pass
    return None

def detect_available_cores() -> int:
    for probe in (_affinity_cores, _cgroup_quota_cores):
        n = probe()
        if n and n > 0:
            return n
    return max(1, os.cpu_count() or 1)

# ------------------------------ Discovery (uses 'topologies') ------------------------------ #

def csc_path_for(ararl_dir: Path, nodes: int, topo_id: str) -> Path:
    p = ararl_dir / f"topologies/N{nodes}/simulation-nodes{nodes}-topo{topo_id}.csc"
    if p.is_file(): return p
    p_single = ararl_dir / f"simulation-nodes{nodes}.csc"
    if p_single.is_file() and topo_id in ("01", "1"): return p_single
    die(f"CSC not found for N{nodes}, topo {topo_id}: {p} or {p_single}")

def pos_header_for(ararl_dir: Path, nodes: int, topo_id: str) -> Path:
    p = ararl_dir / f"topologies/N{nodes}/positions-simulation-nodes{nodes}-topo{topo_id}.h"
    if p.is_file(): return p
    p_single = ararl_dir / f"positions-simulation-nodes{nodes}.h"
    if p_single.is_file() and topo_id in ("01", "1"): return p_single
    die(f"Positions header not found for N{nodes}, topo {topo_id}: {p} or {p_single}")

def makefile_for_ppm(ararl_dir: Path, ppm: int) -> Path:
    p = ararl_dir / f"Makefile-ppm{ppm}"
    if not p.is_file(): die(f"Makefile for ppm {ppm} not found: {p}")
    return p

# ------------------------------ Health check ------------------------------ #

def basic_log_health(log_path: Path, expected_nodes: int) -> Tuple[bool, Dict[str, int]]:
    stats = {"wrapup": 0, "sink_summary": 0}
    if not log_path.is_file(): return (False, stats)
    try:
        with log_path.open('r', encoding='utf-8', errors='ignore') as f:
            for line in f:
                if "WRAPUP node_id=" in line: stats["wrapup"] += 1
                elif "SINK_SUMMARY node=" in line: stats["sink_summary"] += 1
    except Exception:
        return (False, stats)
    # Robust tolerance: expect approx one line per non-sink node; allow some missing
    ok_wrap = stats["wrapup"] >= max(1, int(0.3 * (expected_nodes - 1)))
    ok_sink = stats["sink_summary"] >= max(1, int(0.3 * (expected_nodes - 1)))
    return (ok_wrap and ok_sink), stats

# ------------------------------ Log parsing (paper metrics only) ------------------------------ #

@dataclass
class RunMetrics:
    e2e_latency: List[float] = field(default_factory=list)   # ms, per-node AvgE2E
    nlt: Optional[float] = None                              # ms, first END_ENERGY
    qlr: List[float] = field(default_factory=list)           # QLoss/(QLoss+Fwd) per node
    prr: Optional[float] = None                              # total recv / total gen
    total_gen: int = 0
    total_fw: int = 0
    total_recv: int = 0
    total_qloss: int = 0
    node_count: int = 0

def parse_log(log_path: Path) -> Optional[RunMetrics]:
    if not log_path.is_file(): return None
    m = RunMetrics()
    node_data: Dict[int, Dict] = {}
    first_death_ms: Optional[int] = None
    try:
        with log_path.open('r', encoding='utf-8', errors='ignore') as f:
            for line in f:
                if "WRAPUP node_id=" in line:
                    g = re.search(r'node_id=(\d+)', line)
                    if not g: continue
                    nid = int(g.group(1))
                    if nid == 1: continue
                    node_data.setdefault(nid, {})
                    ms = re.search(r'end_ms=(\d+)', line)
                    if ms: node_data[nid]['end_ms'] = int(ms.group(1))
                    rs = re.search(r'reason=([A-Za-z_]+)', line)
                    if rs:
                        reason = rs.group(1).lower()
                        if ('energy' in reason) and ('end_ms' in node_data[nid]):
                            ed = node_data[nid]['end_ms']
                            if first_death_ms is None or ed < first_death_ms:
                                first_death_ms = ed
                    gg = re.search(r'Gen=(\d+)', line)
                    if gg:
                        val = int(gg.group(1))
                        node_data[nid]['gen'] = val
                        m.total_gen += val
                    fw = re.search(r'Fwd=(\d+)', line)
                    if fw:
                        val = int(fw.group(1))
                        node_data[nid]['fwd'] = val
                        m.total_fw += val
                    ql = re.search(r'QLoss=(\d+)', line)
                    if ql:
                        val = int(ql.group(1))
                        node_data[nid]['qloss'] = val
                        m.total_qloss += val
                elif "SINK_SUMMARY node=" in line:
                    g = re.search(r'node=(\d+)', line)
                    if not g: continue
                    nid = int(g.group(1))
                    if nid == 1: continue
                    node_data.setdefault(nid, {})
                    rv = re.search(r'Recv=(\d+)', line)
                    if rv:
                        rcv = int(rv.group(1))
                        node_data[nid]['recv'] = rcv
                        m.total_recv += rcv
                    av = re.search(r'AvgE2E=(\d+(?:\.\d+)?)(?:ms)?', line)
                    if av:
                        e = float(av.group(1))
                        node_data[nid]['e2e'] = e
                        m.e2e_latency.append(e)
    except Exception as e:
        print(f"[WARN] parse failed {log_path}: {e}")
        return None

    m.node_count = len(node_data)
    if first_death_ms is not None:
        m.nlt = float(first_death_ms)
    elif node_data:
        max_end = max((n.get('end_ms', 0) for n in node_data.values()), default=0)
        if max_end > 0: m.nlt = float(max_end)
    for nid, d in node_data.items():
        ql  = d.get('qloss', 0)
        fwd = d.get('fwd', 0)
        attempts = ql + fwd
        if attempts > 0:
            m.qlr.append(ql / attempts)   # QLoss/(QLoss+Fwd)
    if m.total_gen > 0:
        m.prr = m.total_recv / m.total_gen
    return m

@dataclass
class AggregatedMetrics:
    e2e_mean: Optional[float]=None; e2e_std: Optional[float]=None; e2e_min: Optional[float]=None; e2e_max: Optional[float]=None
    nlt_mean: Optional[float]=None; nlt_std: Optional[float]=None; nlt_min: Optional[float]=None; nlt_max: Optional[float]=None
    qlr_mean: Optional[float]=None; qlr_std: Optional[float]=None; qlr_min: Optional[float]=None; qlr_max: Optional[float]=None
    prr_mean: Optional[float]=None; prr_std: Optional[float]=None; prr_min: Optional[float]=None; prr_max: Optional[float]=None
    run_count: int = 0; valid_runs: int = 0

def aggregate_metrics(items: List[RunMetrics]) -> AggregatedMetrics:
    agg = AggregatedMetrics()
    vals_e2e: List[float]=[]; vals_nlt: List[float]=[]; vals_qlr: List[float]=[]; vals_prr: List[float]=[]
    agg.run_count = len(items); agg.valid_runs = len([x for x in items if x is not None])
    for m in items:
        if m is None: continue
        vals_e2e.extend(m.e2e_latency)
        if m.nlt is not None: vals_nlt.append(m.nlt)
        vals_qlr.extend(m.qlr)
        if m.prr is not None: vals_prr.append(m.prr)
    if vals_e2e:
        agg.e2e_mean = statistics.mean(vals_e2e)
        agg.e2e_std  = statistics.stdev(vals_e2e) if len(vals_e2e)>1 else 0.0
        agg.e2e_min, agg.e2e_max = min(vals_e2e), max(vals_e2e)
    if vals_nlt:
        agg.nlt_mean = statistics.mean(vals_nlt)
        agg.nlt_std  = statistics.stdev(vals_nlt) if len(vals_nlt)>1 else 0.0
        agg.nlt_min, agg.nlt_max = min(vals_nlt), max(vals_nlt)
    if vals_qlr:
        agg.qlr_mean = statistics.mean(vals_qlr)
        agg.qlr_std  = statistics.stdev(vals_qlr) if len(vals_qlr)>1 else 0.0
        agg.qlr_min, agg.qlr_max = min(vals_qlr), max(vals_qlr)
    if vals_prr:
        agg.prr_mean = statistics.mean(vals_prr)
        agg.prr_std  = statistics.stdev(vals_prr) if len(vals_prr)>1 else 0.0
        agg.prr_min, agg.prr_max = min(vals_prr), max(vals_prr)
    return agg

def write_aggregated_csv(agg: AggregatedMetrics, out_csv: Path) -> None:
    ensure_dir(out_csv.parent)
    with out_csv.open('w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['Metric','Mean','Std','Min','Max','Unit'])
        if agg.e2e_mean is not None: w.writerow(['E2E', f'{agg.e2e_mean:.2f}', f'{agg.e2e_std:.2f}', f'{agg.e2e_min:.2f}', f'{agg.e2e_max:.2f}', 'ms'])
        if agg.nlt_mean is not None: w.writerow(['NLT', f'{agg.nlt_mean:.2f}', f'{agg.nlt_std:.2f}', f'{agg.nlt_min:.2f}', f'{agg.nlt_max:.2f}', 'ms'])
        if agg.qlr_mean is not None: w.writerow(['QLR', f'{agg.qlr_mean:.4f}', f'{agg.qlr_std:.4f}', f'{agg.qlr_min:.4f}', f'{agg.qlr_max:.4f}', 'ratio'])
        if agg.prr_mean is not None: w.writerow(['PRR', f'{agg.prr_mean:.4f}', f'{agg.prr_std:.4f}', f'{agg.prr_min:.4f}', f'{agg.prr_max:.4f}', 'ratio'])
        w.writerow([]); w.writerow(['Runs', agg.run_count, 'Valid', agg.valid_runs, '', ''])

def write_aggregated_json(agg: AggregatedMetrics, out_json: Path) -> None:
    ensure_dir(out_json.parent)
    data = {"runs":{"total":agg.run_count,"valid":agg.valid_runs},"metrics":{}}
    if agg.e2e_mean is not None: data["metrics"]["E2E"]={"mean":agg.e2e_mean,"std":agg.e2e_std,"min":agg.e2e_min,"max":agg.e2e_max,"unit":"ms"}
    if agg.nlt_mean is not None: data["metrics"]["NLT"]={"mean":agg.nlt_mean,"std":agg.nlt_std,"min":agg.nlt_min,"max":agg.nlt_max,"unit":"ms"}
    if agg.qlr_mean is not None: data["metrics"]["QLR"]={"mean":agg.qlr_mean,"std":agg.qlr_std,"min":agg.qlr_min,"max":agg.qlr_max,"unit":"ratio"}
    if agg.prr_mean is not None: data["metrics"]["PRR"]={"mean":agg.prr_mean,"std":agg.prr_std,"min":agg.prr_min,"max":agg.prr_max,"unit":"ratio"}
    out_json.write_text(json.dumps(data, indent=2), encoding='utf-8')

def append_run_summary_row(base_logs: Path, n:int, ppm:int, topo:str, seed:int, mask:str, m: RunMetrics) -> None:
    csv_path = base_logs / "summary.csv"
    new = not csv_path.exists()
    with csv_path.open("a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["nodes","ppm","topo","seed","mask","prr","qlr","e2e_ms","nlt_ms","gen","recv"])
        prr = m.prr if m.prr is not None else ""
        qlr = (statistics.mean(m.qlr) if m.qlr else "")
        e2e = (statistics.mean(m.e2e_latency) if m.e2e_latency else "")
        nlt = m.nlt if m.nlt is not None else ""
        w.writerow([n, ppm, topo, seed, mask,
                    f"{prr:.6f}" if prr!="" else "",
                    f"{qlr:.9f}" if qlr!="" else "",
                    f"{e2e:.2f}" if e2e!="" else "",
                    int(nlt) if nlt!="" else "",
                    m.total_gen, m.total_recv])

# ------------------------------ One run (isolated workspace) ------------------------------ #

def _clone_workspace(src: Path, dst: Path) -> None:
    if dst.exists(): shutil.rmtree(dst, ignore_errors=True)
    dst.parent.mkdir(parents=True, exist_ok=True)
    # Fast path: rsync with hard-links; fallback to copytree
    try:
        subprocess.check_call(["rsync", "-a", "--delete", "--hard-links", f"{src}/", f"{dst}/"])
    except Exception:
        shutil.copytree(src, dst)

def run_block(cfg: RunnerConfig, n: int, ppm: int, topo: str, seed: int) -> Tuple[bool, str]:
    # Resolve inputs
    csc_src = csc_path_for(cfg.ararl_dir, n, topo)
    pos_src = pos_header_for(cfg.ararl_dir, n, topo)
    mk_src  = makefile_for_ppm(cfg.ararl_dir, ppm)
    if not cfg.mask_file.is_file():
        die(f"Mask file not found: {cfg.mask_file}")

    # Prepare paths
    run_dir = cfg.logs_dir / f"N{n}_PPM{ppm}" / f"topo{topo}" / f"seed{seed}" / cfg.mask_name
    backup_if_exists(run_dir)
    ensure_dir(run_dir)

    # Isolated workspace
    work_dir = cfg.work_root / f"N{n}_PPM{ppm}" / f"topo{topo}" / f"seed{seed}" / cfg.mask_name
    _clone_workspace(cfg.ararl_dir, work_dir)

    # Drop scenario files into workspace
    shutil.copy2(csc_src, work_dir / "simulation.csc")
    shutil.copy2(pos_src, work_dir / "positions-simulation.h")
    shutil.copy2(mk_src,  work_dir / "Makefile")

    # Env
    env = os.environ.copy()
    env.update({
        "NODES": str(n),
        "PPM": str(ppm),
        "TOPOLOGY_ID": topo,
        "TRAFFIC_SEED": str(seed),
        "SIM_SEED": str(cfg.sim_seed),
        "AGENT_SEED": str(cfg.agent_seed),
        "DURATION_SF": str(cfg.duration_sf),
        "WARMUP_SF": str(cfg.warmup_sf),
        "MASK_NAME": cfg.mask_name,
        "MASK_FILE": str(cfg.mask_file.resolve()),
        "AGENT_LOG_PATH": str((work_dir / "agent.log").resolve()),
        # avoid BLAS over-subscription
        "OMP_NUM_THREADS": "1",
        "OPENBLAS_NUM_THREADS": "1",
        "MKL_NUM_THREADS": "1",
        "OPENMP_NUM_THREADS": "1",
    })
    if cfg.gradle_user_home is not None:
        env["GRADLE_USER_HOME"] = str(cfg.gradle_user_home.resolve())
    if cfg.tx_range is not None: env["TX_RANGE"] = str(cfg.tx_range)
    if cfg.int_range is not None: env["INT_RANGE"] = str(cfg.int_range)

    # Command (single-token --args=..., and -p points at gradle root)
    gradlew = cfg.gradle_root / "gradlew"
    cmd = [str(gradlew), "-p", str(cfg.gradle_root), "run", f"--args=--no-gui {work_dir/'simulation.csc'}"]

    # Meta
    meta = {
        "timestamp": now_stamp(),
        "nodes": n, "ppm": ppm, "topology_id": topo, "traffic_seed": seed,
        "sim_seed": cfg.sim_seed, "agent_seed": cfg.agent_seed,
        "duration_sf": cfg.duration_sf, "warmup_sf": cfg.warmup_sf,
        "mask_name": cfg.mask_name, "mask_file": str(cfg.mask_file),
        "mask_enabled": read_mask_enabled(cfg.mask_file),
        "ararl_dir": str(cfg.ararl_dir.resolve()),
        "csc_src": str(csc_src), "pos_header_src": str(pos_src),
        "ppm_makefile_src": str(mk_src), "gradle_root": str(cfg.gradle_root.resolve()),
        "work_dir": str(work_dir.resolve()), "run_dir": str(run_dir.resolve()),
    }
    write_text(run_dir / "run_meta.yaml", yaml_dump(meta))

    if cfg.dry_run:
        print(f"[DRY-RUN] {' '.join(cmd)}")
        return True, str(run_dir)

    # Execute in the workspace (so COOJA.testlog is unique)
    tag = f"N{n}_PPM{ppm}_topo{topo}_seed{seed}_mask={cfg.mask_name}"
    exit_code = sh(cmd, cwd=work_dir, env=env, out_path=run_dir / "runner.log", tag=tag, heartbeat_secs=60)
    if exit_code != 0:
        print(f"[ERR] Runner exited non-zero: code={exit_code} run={run_dir}")
        tail = tail_lines(run_dir / "runner.log", n=cfg.error_log_tail)
        if tail:
            if cfg.error_log_tail == 0:
                print("[ERR] Full runner.log:")
            else:
                print(f"[ERR] Last {cfg.error_log_tail} lines of runner.log:")
            for ln in tail:
                print(f"  {ln}")
        else:
            print("[ERR] runner.log not found or unreadable.")
        if not cfg.keep_work:
            shutil.rmtree(work_dir, ignore_errors=True)
        return False, str(run_dir)

    # Handle logs
    log_src = work_dir / "COOJA.testlog"
    if log_src.exists():
        shutil.move(str(log_src), str(run_dir / "COOJA.testlog"))
    else:
        print(f"[WARN] missing log: {log_src}")

    ok, stats = basic_log_health(run_dir / "COOJA.testlog", expected_nodes=n)
    if not ok:
        print(f"[WARN] Health check failed: WRAPUP={stats['wrapup']} SINK_SUMMARY={stats['sink_summary']} (expected ≈{n-1})")

    agent_src = work_dir / "agent.log"
    if agent_src.exists():
        shutil.copy2(agent_src, run_dir / "agent.log")

    # Cleanup workspace unless requested to keep
    if not cfg.keep_work:
        shutil.rmtree(work_dir, ignore_errors=True)

    return ok, str(run_dir)

# ------------------------------ CLI/Main ------------------------------ #

def parse_args() -> RunnerConfig:
    ap = argparse.ArgumentParser(
        description="Parallel Cooja runner with isolated workspaces + aggregation (paper metrics).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    ap.add_argument("--ararl-dir", type=Path, required=True, help="experiments/ararl dir (contains topologies/, *.c, Makefile-ppm*)")
    ap.add_argument("--logs-dir", type=Path, default=Path("testbed/logs"))
    ap.add_argument("--gradle-root", type=Path, default=Path("contiki-ng/tools/cooja"))
    ap.add_argument("--nodes", type=int, nargs="+", default=[60,80,100])
    ap.add_argument("--ppm", type=int, nargs="+", default=[80,100,120])
    ap.add_argument("--topologies", type=int, default=10, help="Number of topo IDs (01..NN) if --topology-ids not given")
    ap.add_argument("--topology-ids", type=str, nargs="*", help="Explicit topo IDs like 01 02 03")
    ap.add_argument("--mask-file", type=Path, required=True, help="YAML mask file the controller reads")
    ap.add_argument("--mask-name", type=str, default=None, help="Name tag for this mask (folder name). Default: mask file name (stem)")
    ap.add_argument("--duration-sf", type=int, default=180)
    ap.add_argument("--warmup-sf", type=int, default=12)
    ap.add_argument("--sim-seed", type=int, default=67890)
    ap.add_argument("--agent-seed", type=int, default=12345)
    ap.add_argument("--traffic-seeds", type=int, nargs="+", default=[1])
    ap.add_argument("--tx-range", type=float, default=None)
    ap.add_argument("--int-range", type=float, default=None)
    ap.add_argument("--gradle-user-home", type=Path, default=None,
                    help="Gradle user home/cache path. Default: inherit environment (recommended with Docker volume).")
    ap.add_argument("--jobs", type=int, default=0, help="Max concurrent runs. 0 = auto (use all allowed cores)")
    ap.add_argument("--work-root", type=Path, default=Path("testbed/_work"), help="Where per-run temp workspaces go")
    ap.add_argument("--keep-work", action="store_true", help="Keep workspaces (debug)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--error-log-tail", type=int, default=50,
                    help="Lines from end of runner.log to print on non-zero exit. 0 = print full file.")

    a = ap.parse_args()

    # topology id list
    if a.topology_ids:
        topo_ids = a.topology_ids
    else:
        width = max(2, len(str(a.topologies)))   # always at least 2 digits
        topo_ids = [str(i).zfill(width) for i in range(1, a.topologies + 1)]

    # Effective mask name and jobs
    mask_name = a.mask_name or auto_mask_name_from_file(a.mask_file)
    jobs = detect_available_cores() if a.jobs == 0 else max(1, a.jobs)

    return RunnerConfig(
        ararl_dir=a.ararl_dir.resolve(),
        logs_dir=a.logs_dir.resolve(),
        gradle_root=a.gradle_root.resolve(),
        nodes=a.nodes,
        ppms=a.ppm,
        topology_ids=topo_ids,
        mask_name=mask_name,
        mask_file=a.mask_file.resolve(),
        duration_sf=a.duration_sf,
        warmup_sf=a.warmup_sf,
        sim_seed=a.sim_seed,
        agent_seed=a.agent_seed,
        traffic_seeds=a.traffic_seeds,
        tx_range=a.tx_range,
        int_range=a.int_range,
        gradle_user_home=(a.gradle_user_home.resolve() if a.gradle_user_home else None),
        jobs=jobs,
        work_root=a.work_root.resolve(),
        keep_work=a.keep_work,
        dry_run=a.dry_run,
        error_log_tail=max(0, a.error_log_tail),
    )

def main() -> None:
    started_at = time.time()
    cfg = parse_args()

    print("== Cooja Runner v2 (parallel) ==")
    print(f"ARARL dir   : {cfg.ararl_dir}")
    print(f"Logs dir    : {cfg.logs_dir}")
    print(f"Gradle root : {cfg.gradle_root}")
    print(f"Nodes       : {cfg.nodes}")
    print(f"PPM         : {cfg.ppms}")
    print(f"Topologies  : {cfg.topology_ids}")
    print(f"Mask        : {cfg.mask_name} -> {cfg.mask_file}")
    print(f"Mask enabled: {', '.join(read_mask_enabled(cfg.mask_file))}")
    print(f"Gradle home : {cfg.gradle_user_home if cfg.gradle_user_home else '(inherited)'}")
    print(f"Jobs        : {cfg.jobs}")
    print(f"Work root   : {cfg.work_root}")
    print(f"Dry-run     : {cfg.dry_run}")
    print(f"Err log tail: {cfg.error_log_tail} (0=full)")

    # Pre-clean workspace so old aborted runs can't leak anything
    if not cfg.keep_work and cfg.work_root.exists():
        shutil.rmtree(cfg.work_root, ignore_errors=True)

    ensure_dir(cfg.logs_dir)
    ensure_dir(cfg.work_root)

    # Prepare all tasks
    tasks = []
    for n in cfg.nodes:
        for ppm in cfg.ppms:
            for topo in cfg.topology_ids:
                for seed in cfg.traffic_seeds:
                    tasks.append((n, ppm, topo, seed))

    # Run in parallel
    results: Dict[Tuple[int,int,str,int], Tuple[bool,str]] = {}
    with ThreadPoolExecutor(max_workers=cfg.jobs) as ex:
        futs = {ex.submit(run_block, cfg, n, ppm, topo, seed): (n, ppm, topo, seed)
                for (n, ppm, topo, seed) in tasks}
        done_cnt = 0
        for fut in as_completed(futs):
            key = futs[fut]
            try:
                ok, rdir = fut.result()
            except Exception as e:
                ok, rdir = False, ""
                print(f"[ERR] run failed {key}: {e}")
            results[key] = (ok, rdir)
            done_cnt += 1
            print(f"[PROGRESS] {done_cnt}/{len(tasks)} complete")

    mask_metrics: Dict[Tuple[int,int], List[RunMetrics]] = {}
    
    # Parse + aggregate per (nodes, ppm) and append per-run summary
    for (n, ppm, topo, seed), (ok, rdir) in results.items():
        if not rdir:
            continue

        log_path = Path(rdir) / "COOJA.testlog"
        m = parse_log(log_path)

        if not m:
            print(f"[WARN] No parsable metrics in {log_path} (ok={ok})")
            continue

        # even if ok==False, still include it in aggregation
        mask_metrics.setdefault((n, ppm), []).append(m)
        append_run_summary_row(cfg.logs_dir, n, ppm, topo, seed, cfg.mask_name, m)

        if not ok:
            print(f"[WARN] Included run despite health-check fail: N={n} PPM={ppm} topo={topo} seed={seed}")

    print("\n" + "="*78)
    print("AGGREGATING RESULTS")
    print("="*78)

    for (n, ppm), lst in sorted(mask_metrics.items()):
        agg = aggregate_metrics(lst)
        base = cfg.logs_dir / f"N{n}_PPM{ppm}" / cfg.mask_name
        write_aggregated_csv(agg, base / "aggregated_results.csv")
        write_aggregated_json(agg, base / "aggregated_results.json")
        print(f"\nMask={cfg.mask_name}  N={n}  PPM={ppm}")
        print(f"  Runs: {agg.valid_runs}/{agg.run_count}")
        if agg.e2e_mean is not None: print(f"  E2E: {agg.e2e_mean:.2f} ± {agg.e2e_std:.2f} ms (min={agg.e2e_min:.2f}, max={agg.e2e_max:.2f})")
        if agg.nlt_mean is not None: print(f"  NLT: {agg.nlt_mean:.2f} ± {agg.nlt_std:.2f} ms (min={agg.nlt_min:.2f}, max={agg.nlt_max:.2f})")
        if agg.qlr_mean is not None: print(f"  QLR: {agg.qlr_mean:.4f} ± {agg.qlr_std:.4f} (min={agg.qlr_min:.4f}, max={agg.qlr_max:.4f})")
        if agg.prr_mean is not None: print(f"  PRR: {agg.prr_mean:.4f} ± {agg.prr_std:.4f} (min={agg.prr_min:.4f}, max={agg.prr_max:.4f})")
        print(f"  → {base}")

    total = len(tasks); ok_count = sum(1 for v in results.values() if v[0])
    elapsed_s = int(time.time() - started_at)
    hh, rem = divmod(elapsed_s, 3600)
    mm, ss = divmod(rem, 60)
    print("\n" + "="*78)
    print(f"DONE. Runs attempted: {total}, passed basic health: {ok_count}.")
    print(f"Elapsed wall time: {hh:02d}:{mm:02d}:{ss:02d} ({elapsed_s}s)")
    print("="*78)

    # Cleanup global work root unless kept for debug
    if not cfg.keep_work:
        try: shutil.rmtree(cfg.work_root, ignore_errors=True)
        except Exception: pass

if __name__ == "__main__":
    main()
