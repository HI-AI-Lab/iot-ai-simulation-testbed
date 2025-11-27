#!/usr/bin/env python3
"""
Cooja Runner (v1.1) — matches /topologies/N*/ layout and real log lines

- Looks up CSC/positions in: {ARARL_DIR}/topologies/N{nodes}/simulation-nodes{nodes}-topo{XX}.csc
                            and positions-simulation-nodes{nodes}-topo{XX}.h
- Picks Makefile-ppm{ppm}
- Launches Cooja headless via Gradle
- Moves each run’s COOJA.testlog to logs/{N}_PPM{ppm}/topo{XX}/seed{S}/{mask}/
- Writes run_meta.yaml
- Parses log to compute per-run PRR, QLR, E2E (recv-weighted), NLT (first energy death)
- Appends per-run metrics to logs/summary.csv
- Aggregates per (N, PPM, mask) across all topologies/seeds → aggregated_results.csv / .json

Usage (example):
  python cooja_runner_v1.py \
    --ararl-dir testbed/experiments/ararl \
    --logs-dir  testbed/logs \
    --nodes 60 80 100 \
    --ppm 80 100 120 \
    --topologies 10 \
    --masks baseline \
    --mask-files baseline:masks/mask-etx.yaml \
    --duration-sf 180 --warmup-sf 12 \
    --sim-seed 67890 --agent-seed 12345 \
    --traffic-seeds 1 \
    [--gradle-root contiki-ng/tools/cooja] \
    [--dry-run]
"""
from __future__ import annotations

import argparse, csv, hashlib, json, os, re, shutil, statistics, subprocess, sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

try:
    import yaml  # pyyaml
except Exception:
    yaml = None

# ------------------------------ Data types ------------------------------ #

@dataclass
class RunnerConfig:
    ararl_dir: Path
    logs_dir: Path
    gradle_root: Path
    nodes: List[int]
    ppms: List[int]
    topology_count: int
    topology_ids: List[str]
    masks: List[str]
    mask_files: Dict[str, Path]
    duration_sf: int
    warmup_sf: int
    sim_seed: int
    agent_seed: int
    traffic_seeds: List[int]
    tx_range: float | None
    int_range: float | None
    dry_run: bool

# ------------------------------ Helpers ------------------------------ #

def die(msg: str, code: int = 2) -> None:
    print(f"[FATAL] {msg}", file=sys.stderr); sys.exit(code)

def sh(cmd: List[str], cwd: Path | None = None, env: Dict[str, str] | None = None) -> int:
    print(f"[RUN] {' '.join(cmd)} (cwd={cwd})")
    try:
        proc = subprocess.Popen(cmd, cwd=str(cwd) if cwd else None, env=env)
        return proc.wait()
    except FileNotFoundError:
        return 127

def ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)

def yaml_dump(d: Dict) -> str:
    if yaml is None:
        return "\n".join(f"{k}: {v}" for k, v in d.items())
    return yaml.safe_dump(d, sort_keys=False, allow_unicode=True)

def now_stamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

# ------------------------------ Discovery (ADAPTED) ------------------------------ #

def csc_path_for(ararl_dir: Path, nodes: int, topo_id: str) -> Path:
    # Your real structure: testbed/experiments/ararl/topologies/N60/simulation-nodes60-topo01.csc
    p = ararl_dir / f"topologies/N{nodes}/simulation-nodes{nodes}-topo{topo_id}.csc"
    if p.is_file(): return p
    # Fallback (single CSC per N)
    p_single = ararl_dir / f"simulation-nodes{nodes}.csc"
    if p_single.is_file():
        if topo_id not in ("01","1"):
            die(f"Missing placement for N{nodes} topo{topo_id}: {p}")
        return p_single
    die(f"CSC not found: expected {p} or {p_single}")

def pos_header_for(ararl_dir: Path, nodes: int, topo_id: str) -> Path:
    p = ararl_dir / f"topologies/N{nodes}/positions-simulation-nodes{nodes}-topo{topo_id}.h"
    if p.is_file(): return p
    p_single = ararl_dir / f"positions-simulation-nodes{nodes}.h"
    if p_single.is_file():
        if topo_id not in ("01","1"):
            die(f"Missing positions header for N{nodes} topo{topo_id}: {p}")
        return p_single
    die(f"Positions header not found: expected {p} or {p_single}")

def makefile_for_ppm(ararl_dir: Path, ppm: int) -> Path:
    p = ararl_dir / f"Makefile-ppm{ppm}"
    if not p.is_file():
        die(f"Makefile-ppm{ppm} not found at {p}")
    return p

# ------------------------------ Log Parsing (FIXED) ------------------------------ #

@dataclass
class RunMetrics:
    # Weighted by Recv count
    e2e_weighted_sum: float = 0.0
    total_recv: int = 0
    # Also keep unweighted per-node means if you want later
    e2e_latency: List[float] = field(default_factory=list)

    nlt: Optional[float] = None   # first energy death ms (fallback: sim end)
    total_gen: int = 0
    total_qloss: int = 0
    prr: Optional[float] = None
    qlr: List[float] = field(default_factory=list)  # per-node QLoss/Gen
    node_count: int = 0

    @property
    def e2e_mean(self) -> Optional[float]:
        return (self.e2e_weighted_sum / self.total_recv) if self.total_recv > 0 else None

def parse_log(log_path: Path) -> Optional[RunMetrics]:
    if not log_path.is_file(): return None

    m = RunMetrics()
    node_seen: Dict[int, Dict] = {}
    first_energy_ms: Optional[int] = None

    try:
        with log_path.open('r', encoding='utf-8', errors='ignore') as f:
            for line in f:
                # Real lines look like:
                # 5520940000  1 [INFO: App] SINK_SUMMARY node=2 Recv=123 AvgE2E=4005ms ...
                # 460623000   16 [INFO: App] WRAPUP node_id=16 reason=energy end_ms=... Gen=... QLoss=...
                if "WRAPUP node_id=" in line:
                    nid_m = re.search(r'node_id=(\d+)', line)
                    if not nid_m: continue
                    nid = int(nid_m.group(1))
                    if nid == 1:  # skip sink
                        continue
                    node_seen.setdefault(nid, {})

                    # end_ms
                    ms_m = re.search(r'end_ms=(\d+)', line)
                    if ms_m:
                        node_seen[nid]['end_ms'] = int(ms_m.group(1))

                    # reason
                    rs_m = re.search(r'reason=([A-Za-z_]+)', line)
                    if rs_m:
                        node_seen[nid]['reason'] = rs_m.group(1).lower()
                        if node_seen[nid]['reason'] == 'energy':
                            ems = node_seen[nid].get('end_ms', None)
                            if ems is not None and (first_energy_ms is None or ems < first_energy_ms):
                                first_energy_ms = ems

                    # Gen / QLoss
                    g_m = re.search(r'Gen=(\d+)', line)
                    if g_m:
                        g = int(g_m.group(1)); node_seen[nid]['gen'] = g; m.total_gen += g
                    ql_m = re.search(r'QLoss=(\d+)', line)
                    if ql_m:
                        ql = int(ql_m.group(1)); node_seen[nid]['qloss'] = ql; m.total_qloss += ql

                elif "SINK_SUMMARY node=" in line:
                    nid_m = re.search(r'node=(\d+)', line)
                    if not nid_m: continue
                    nid = int(nid_m.group(1))
                    if nid == 1:  # skip sink
                        continue
                    node_seen.setdefault(nid, {})

                    r_m = re.search(r'Recv=(\d+)', line)
                    if r_m:
                        recv = int(r_m.group(1))
                        node_seen[nid]['recv'] = recv
                        m.total_recv += recv

                    e2e_m = re.search(r'AvgE2E=(\d+)ms', line)
                    if e2e_m:
                        e2e = float(e2e_m.group(1))
                        node_seen[nid]['e2e'] = e2e
                        m.e2e_latency.append(e2e)
                        if 'recv' in node_seen[nid]:
                            m.e2e_weighted_sum += node_seen[nid]['recv'] * e2e

    except Exception as e:
        print(f"[WARN] Failed to parse {log_path}: {e}")
        return None

    m.node_count = len(node_seen)

    # NLT
    if first_energy_ms is not None:
        m.nlt = float(first_energy_ms)
    else:
        # fallback: latest end_ms (sim end)
        max_end = 0
        for nd in node_seen.values():
            max_end = max(max_end, int(nd.get('end_ms', 0)))
        m.nlt = float(max_end) if max_end > 0 else None

    # QLR per node
    for nd in node_seen.values():
        g = nd.get('gen', 0); ql = nd.get('qloss', 0)
        if g > 0:
            m.qlr.append(ql / g)

    # PRR
    if m.total_gen > 0:
        m.prr = m.total_recv / m.total_gen

    return m

# ------------------------------ Health checks (FIXED) ------------------------------ #

def basic_log_health(log_path: Path, expected_nodes: int) -> Tuple[bool, Dict[str, int]]:
    stats = {"wrapup": 0, "sink_summary": 0}
    if not log_path.is_file(): return (False, stats)
    try:
        with log_path.open('r', encoding='utf-8', errors='ignore') as f:
            for line in f:
                if "WRAPUP node_id=" in line: stats["wrapup"] += 1
                if "SINK_SUMMARY node=" in line: stats["sink_summary"] += 1
    except Exception:
        return (False, stats)
    # Expect roughly: wrapup == expected_nodes (incl. sink? your WRAPUP skips sink)
    # and sink summaries ~= expected_nodes - 1. Be lenient.
    ok_wrap = (stats["wrapup"] >= expected_nodes - 5)
    ok_sink = (stats["sink_summary"] >= expected_nodes - 5)
    return (ok_wrap and ok_sink), stats

# ------------------------------ Aggregation ------------------------------ #

@dataclass
class AggregatedMetrics:
    e2e_mean: Optional[float] = None
    e2e_std: Optional[float] = None
    e2e_min: Optional[float] = None
    e2e_max: Optional[float] = None

    nlt_mean: Optional[float] = None
    nlt_std: Optional[float] = None
    nlt_min: Optional[float] = None
    nlt_max: Optional[float] = None

    qlr_mean: Optional[float] = None
    qlr_std: Optional[float] = None
    qlr_min: Optional[float] = None
    qlr_max: Optional[float] = None

    prr_mean: Optional[float] = None
    prr_std: Optional[float] = None
    prr_min: Optional[float] = None
    prr_max: Optional[float] = None

    run_count: int = 0
    valid_runs: int = 0

def aggregate_metrics(metrics_list: List[RunMetrics]) -> AggregatedMetrics:
    agg = AggregatedMetrics()
    vals_e2e, vals_nlt, vals_qlr, vals_prr = [], [], [], []

    for m in metrics_list:
        if m is None: continue
        if m.e2e_mean is not None: vals_e2e.append(m.e2e_mean)
        if m.nlt        is not None: vals_nlt.append(m.nlt)
        vals_qlr.extend(m.qlr)
        if m.prr        is not None: vals_prr.append(m.prr)

    agg.run_count = len(metrics_list)
    agg.valid_runs = sum(1 for m in metrics_list if m is not None)

    def pack(a):
        if not a: return (None, None, None, None)
        return (statistics.mean(a),
                statistics.stdev(a) if len(a) > 1 else 0.0,
                min(a), max(a))

    agg.e2e_mean, agg.e2e_std, agg.e2e_min, agg.e2e_max = pack(vals_e2e)
    agg.nlt_mean, agg.nlt_std, agg.nlt_min, agg.nlt_max = pack(vals_nlt)
    agg.qlr_mean, agg.qlr_std, agg.qlr_min, agg.qlr_max = pack(vals_qlr)
    agg.prr_mean, agg.prr_std, agg.prr_min, agg.prr_max = pack(vals_prr)
    return agg

def write_aggregated_csv(agg: AggregatedMetrics, path: Path) -> None:
    ensure_dir(path.parent)
    with path.open('w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['Metric','Mean','Std','Min','Max','Unit'])
        if agg.e2e_mean is not None: w.writerow(['E2E', f'{agg.e2e_mean:.2f}', f'{agg.e2e_std:.2f}', f'{agg.e2e_min:.2f}', f'{agg.e2e_max:.2f}', 'ms'])
        if agg.nlt_mean is not None: w.writerow(['NLT', f'{agg.nlt_mean:.0f}', f'{agg.nlt_std:.0f}', f'{agg.nlt_min:.0f}', f'{agg.nlt_max:.0f}', 'ms'])
        if agg.qlr_mean is not None: w.writerow(['QLR', f'{agg.qlr_mean:.6f}', f'{agg.qlr_std:.6f}', f'{agg.qlr_min:.6f}', f'{agg.qlr_max:.6f}', 'ratio'])
        if agg.prr_mean is not None: w.writerow(['PRR', f'{agg.prr_mean:.6f}', f'{agg.prr_std:.6f}', f'{agg.prr_min:.6f}', f'{agg.prr_max:.6f}', 'ratio'])
        w.writerow([])
        w.writerow(['Runs', agg.run_count, 'Valid', agg.valid_runs, '', ''])

def write_aggregated_json(agg: AggregatedMetrics, path: Path, mask: str, n: int, ppm: int) -> None:
    ensure_dir(path.parent)
    out = {
        "mask": mask, "nodes": n, "ppm": ppm,
        "runs": {"total": agg.run_count, "valid": agg.valid_runs},
        "metrics": {}
    }
    def put(name, mean, sd, mn, mx, unit):
        if mean is None: return
        out["metrics"][name] = {"mean": mean, "std": sd, "min": mn, "max": mx, "unit": unit}
    put("E2E", agg.e2e_mean, agg.e2e_std, agg.e2e_min, agg.e2e_max, "ms")
    put("NLT", agg.nlt_mean, agg.nlt_std, agg.nlt_min, agg.nlt_max, "ms")
    put("QLR", agg.qlr_mean, agg.qlr_std, agg.qlr_min, agg.qlr_max, "ratio")
    put("PRR", agg.prr_mean, agg.prr_std, agg.prr_min, agg.prr_max, "ratio")
    path.write_text(json.dumps(out, indent=2), encoding="utf-8")

# ------------------------------ Per-run summary CSV ------------------------------ #

def append_run_summary_row(base_logs: Path, n:int, ppm:int, topo:str, seed:int, mask:str, rm: RunMetrics) -> None:
    csv_path = base_logs / "summary.csv"
    header = ["nodes","ppm","topo","seed","mask","prr","qlr","e2e_ms","nlt_ms","gen","recv"]
    new = not csv_path.exists()
    with csv_path.open("a", newline="") as f:
        w = csv.writer(f)
        if new: w.writerow(header)
        prr = rm.prr if rm.prr is not None else ""
        qlr = (statistics.mean(rm.qlr) if rm.qlr else "")
        e2e = rm.e2e_mean if rm.e2e_mean is not None else ""
        nlt = rm.nlt if rm.nlt is not None else ""
        w.writerow([n, ppm, topo, seed, mask,
                    f"{prr:.6f}" if prr!="" else "",
                    f"{qlr:.9f}" if qlr!="" else "",
                    f"{e2e:.2f}" if e2e!="" else "",
                    int(nlt) if nlt!="" else "",
                    rm.total_gen, rm.total_recv])

# ------------------------------ Runner core ------------------------------ #

def run_block(cfg: RunnerConfig, nodes: int, ppm: int, topo_id: str, traffic_seed: int, mask_name: str) -> Tuple[bool, str]:
    ararl_dir = cfg.ararl_dir
    csc_src = csc_path_for(ararl_dir, nodes, topo_id)
    pos_src = pos_header_for(ararl_dir, nodes, topo_id)
    mk_src  = makefile_for_ppm(ararl_dir, ppm)
    mask_file = cfg.mask_files.get(mask_name)
    if mask_file is None or not mask_file.is_file():
        die(f"Mask file missing for '{mask_name}': {mask_file}")

    run_dir = cfg.logs_dir / f"N{nodes}_PPM{ppm}" / f"topo{topo_id}" / f"seed{traffic_seed}" / mask_name
    ensure_dir(run_dir)

    sim_csc = ararl_dir / "simulation.csc"
    pos_hdr = ararl_dir / "positions-simulation.h"
    mk_dst  = ararl_dir / "Makefile"

    # clean build dirs
    for d in (ararl_dir / "rpl", ararl_dir / "build"):
        if d.exists(): shutil.rmtree(d, ignore_errors=True)

    # stage scenario
    shutil.copy2(csc_src, sim_csc)
    shutil.copy2(pos_src, pos_hdr)
    shutil.copy2(mk_src,  mk_dst)

    # env for ScriptRunner/Agent
    env = os.environ.copy()
    env.update({
        "NODES": str(nodes), "PPM": str(ppm), "TOPOLOGY_ID": topo_id,
        "TRAFFIC_SEED": str(traffic_seed),
        "SIM_SEED": str(cfg.sim_seed), "AGENT_SEED": str(cfg.agent_seed),
        "DURATION_SF": str(cfg.duration_sf), "WARMUP_SF": str(cfg.warmup_sf),
        "MASK_NAME": mask_name, "MASK_FILE": str(mask_file.resolve()),
    })
    if cfg.tx_range is not None: env["TX_RANGE"] = str(cfg.tx_range)
    if cfg.int_range is not None: env["INT_RANGE"] = str(cfg.int_range)

    # run meta
    meta = {
        "timestamp": now_stamp(), "nodes": nodes, "ppm": ppm,
        "topology_id": topo_id, "traffic_seed": traffic_seed,
        "sim_seed": cfg.sim_seed, "agent_seed": cfg.agent_seed,
        "duration_sf": cfg.duration_sf, "warmup_sf": cfg.warmup_sf,
        "mask_name": mask_name, "mask_file": str(mask_file),
        "ararl_dir": str(ararl_dir.resolve()),
        "csc_src": str(csc_src), "pos_header_src": str(pos_src),
        "ppm_makefile_src": str(mk_src),
        "gradle_root": str(cfg.gradle_root.resolve()),
    }
    (run_dir / "run_meta.yaml").write_text(yaml_dump(meta), encoding="utf-8")

    # command
    gradlew = cfg.gradle_root / "gradlew"
    cmd = [str(gradlew), "-p", str(cfg.gradle_root), "run", "--args", f"--no-gui {sim_csc}"]

    if cfg.dry_run:
        print(f"[DRY-RUN] {' '.join(cmd)}"); return True, str(run_dir)

    exit_code = sh(cmd, cwd=Path("/workspace") if Path("/workspace").exists() else None, env=env)

    # move log
    cooja_log = Path.cwd() / "COOJA.testlog"
    if cooja_log.exists():
        shutil.move(str(cooja_log), str(run_dir / "COOJA.testlog"))
    else:
        print(f"[WARN] COOJA.testlog not found in {Path.cwd()}")

    ok, stats = basic_log_health(run_dir / "COOJA.testlog", expected_nodes=nodes)
    if not ok:
        print(f"[WARN] Health check: WRAPUP={stats['wrapup']} SINK_SUMMARY={stats['sink_summary']} (expected ≈{nodes-1})")

    # cleanup staging
    for p in (mk_dst, sim_csc, pos_hdr):
        try:
            if p.exists(): p.unlink()
        except Exception: pass
    for d in (ararl_dir / "rpl", ararl_dir / "build"):
        if d.exists(): shutil.rmtree(d, ignore_errors=True)

    return (exit_code == 0), str(run_dir)

# ------------------------------ CLI / Main ------------------------------ #

def parse_args() -> RunnerConfig:
    ap = argparse.ArgumentParser(
        description="Run Cooja simulations for (nodes×ppm×topology×seed×mask), sequential v1.1",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    ap.add_argument("--ararl-dir", type=Path, required=True)
    ap.add_argument("--logs-dir", type=Path, default=Path("testbed/logs"))
    ap.add_argument("--gradle-root", type=Path, default=Path("contiki-ng/tools/cooja"))
    ap.add_argument("--nodes", type=int, nargs="+", default=[60,80,100])
    ap.add_argument("--ppm", type=int, nargs="+", default=[80,100,120])
    ap.add_argument("--topologies", type=int, default=10)
    ap.add_argument("--topology-ids", type=str, nargs="*")
    ap.add_argument("--masks", type=str, default="baseline")
    ap.add_argument("--mask-files", type=str, required=True,
                    help="name:file pairs, comma-separated. e.g. baseline:masks/mask-etx.yaml,cand:masks/mask-x.yaml")
    ap.add_argument("--duration-sf", type=int, default=180)
    ap.add_argument("--warmup-sf", type=int, default=12)
    ap.add_argument("--sim-seed", type=int, default=67890)
    ap.add_argument("--agent-seed", type=int, default=12345)
    ap.add_argument("--traffic-seeds", type=int, nargs="+", default=[1])
    ap.add_argument("--tx-range", type=float, default=None)
    ap.add_argument("--int-range", type=float, default=None)
    ap.add_argument("--dry-run", action="store_true")

    args = ap.parse_args()

    if args.topology_ids:
        topo_ids = args.topology_ids
    else:
        width = len(str(args.topologies))
        topo_ids = [str(i).zfill(width) for i in range(1, args.topologies+1)]

    masks = [m for m in args.masks.split(":") if m]
    mapping: Dict[str, Path] = {}
    for pair in args.mask_files.split(","):
        pair = pair.strip()
        if not pair: continue
        name, file = pair.split(":", 1)
        mapping[name.strip()] = Path(file.strip())

    missing = [m for m in masks if m not in mapping]
    if missing:
        die(f"Mask files missing for: {missing}. Provided: {mapping}")

    return RunnerConfig(
        ararl_dir=args.ararl_dir.resolve(),
        logs_dir=args.logs_dir.resolve(),
        gradle_root=args.gradle_root.resolve(),
        nodes=args.nodes,
        ppms=args.ppm,
        topology_count=args.topologies,
        topology_ids=topo_ids,
        masks=masks,
        mask_files=mapping,
        duration_sf=args.duration_sf,
        warmup_sf=args.warmup_sf,
        sim_seed=args.sim_seed,
        agent_seed=args.agent_seed,
        traffic_seeds=args.traffic_seeds,
        tx_range=args.tx_range,
        int_range=args.int_range,
        dry_run=args.dry_run,
    )

def main() -> None:
    cfg = parse_args()
    print("== Cooja Runner v1.1 ==")
    print(f"ARARL dir  : {cfg.ararl_dir}")
    print(f"Logs dir   : {cfg.logs_dir}")
    print(f"Gradle root: {cfg.gradle_root}")
    print(f"Nodes      : {cfg.nodes}")
    print(f"PPM        : {cfg.ppms}")
    print(f"Topologies : {cfg.topology_ids}")
    print(f"Masks      : {cfg.masks}")
    print(f"Traffic sd : {cfg.traffic_seeds}")
    print(f"DurationSF : {cfg.duration_sf} (warmup {cfg.warmup_sf})")
    print(f"Dry-run    : {cfg.dry_run}")

    ensure_dir(cfg.logs_dir)
    t0 = datetime.now()
    total = ok_count = 0

    # collect for aggregation: (n,ppm,mask) -> [RunMetrics...]
    bucket: Dict[Tuple[int,int,str], List[RunMetrics]] = {}

    for n in cfg.nodes:
        for ppm in cfg.ppms:
            for topo in cfg.topology_ids:
                for seed in cfg.traffic_seeds:
                    for mask in cfg.masks:
                        total += 1
                        print("-"*78)
                        print(f"RUN {total}: N={n} PPM={ppm} topo={topo} seed={seed} mask={mask}")
                        success, rdir = run_block(cfg, n, ppm, topo, seed, mask)
                        print(f"Completed: success={success} → {rdir}")
                        logp = Path(rdir) / "COOJA.testlog"
                        rm = parse_log(logp) if logp.exists() else None
                        if success and rm is not None:
                            ok_count += 1
                            append_run_summary_row(cfg.logs_dir, n, ppm, topo, seed, mask, rm)
                            bucket.setdefault((n,ppm,mask), []).append(rm)
                            # brief per-run print
                            prr = rm.prr if rm.prr is not None else 0.0
                            qlr = statistics.mean(rm.qlr) if rm.qlr else 0.0
                            e2e = rm.e2e_mean if rm.e2e_mean is not None else 0.0
                            nlt = int(rm.nlt) if rm.nlt is not None else 0
                            print(f"  PRR={prr:.6f}  QLR={qlr:.9f}  E2E_mean_ms={e2e:.2f}  NLT_first_energy_ms={nlt}")
                        else:
                            print("[WARN] Run failed or metrics missing; not counted.")

    print("\n" + "="*78)
    print("AGGREGATING RESULTS...")
    print("="*78)

    for (n, ppm, mask), runs in sorted(bucket.items()):
        agg = aggregate_metrics(runs)
        base = cfg.logs_dir / f"N{n}_PPM{ppm}" / mask
        ensure_dir(base)
        write_aggregated_csv(agg, base / "aggregated_results.csv")
        write_aggregated_json(agg, base / "aggregated_results.json", mask, n, ppm)
        print(f"\nMask: {mask} | Nodes: {n} | PPM: {ppm}")
        print(f"  Runs: {agg.valid_runs}/{agg.run_count} valid")
        if agg.e2e_mean is not None:
            print(f"  E2E: {agg.e2e_mean:.2f} ± {agg.e2e_std:.2f} ms  [min {agg.e2e_min:.2f}, max {agg.e2e_max:.2f}]")
        if agg.nlt_mean is not None:
            print(f"  NLT: {agg.nlt_mean:.0f} ± {agg.nlt_std:.0f} ms [min {agg.nlt_min:.0f}, max {agg.nlt_max:.0f}]")
        if agg.qlr_mean is not None:
            print(f"  QLR: {agg.qlr_mean:.6f} ± {agg.qlr_std:.6f} [min {agg.qlr_min:.6f}, max {agg.qlr_max:.6f}]")
        if agg.prr_mean is not None:
            print(f"  PRR: {agg.prr_mean:.6f} ± {agg.prr_std:.6f} [min {agg.prr_min:.6f}, max {agg.prr_max:.6f}]")
        print(f"  → {base}")

    dt = datetime.now() - t0
    print("\n" + "="*78)
    print(f"DONE. Runs attempted: {total}, passed basic health: {ok_count}. Elapsed: {dt}.")

if __name__ == "__main__":
    main()
