#!/usr/bin/env python3
"""
Cooja Runner (v1) — sequential, reproducible, upgradeable

Goal (Phase 1): Replace the bash script with a clean Python runner that
- iterates scenarios (nodes × ppm) and pre-generated topologies,
- swaps the correct CSC + positions header per run,
- picks the right Makefile per PPM,
- launches Cooja headless via Gradle,
- stores logs in a structured per-run directory,
- writes minimal run_meta.yaml for provenance,
- performs basic health checks (WRAPUP/SINK_SUMMARY),
- can be extended later (Phase 2) for concurrency, parsing, and aggregation.

Usage:
  python cooja_runner_v1.py \
    --ararl-dir testbed/experiments/ararl \
    --logs-dir  testbed/logs \
    --nodes 60 80 100 \
    --ppm 80 100 120 \
    --topologies 10 \
    --masks baseline:candidate \
    --mask-files baseline:masks/mask-etx.yaml,candidate:masks/mask-x.yaml \
    --duration-sf 180 --warmup-sf 12 \
    --sim-seed 67890 --agent-seed 12345 \
    --traffic-seeds 1 \
    [--gradle-root contiki-ng/tools/cooja] \
    [--dry-run]

Notes:
- This v1 runs **sequentially** to avoid workspace contention (your bash script also used a shared ARARL dir).
- For Phase 2 (parallel), either:
  (A) use per-run temporary workspaces (copy ARARL into a temp dir), or
  (B) refactor your CSCs to reference per-run unique headers to avoid clashes.

Author: (you)
"""
from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import textwrap
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple

try:
    import yaml  # pyyaml
except Exception as e:
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
    print(f"[FATAL] {msg}", file=sys.stderr)
    sys.exit(code)


def sh(cmd: List[str], cwd: Path | None = None, env: Dict[str, str] | None = None) -> int:
    """Run a shell command with live stdout/stderr. Return exit code."""
    print(f"[RUN] {' '.join(cmd)} (cwd={cwd})")
    try:
        proc = subprocess.Popen(cmd, cwd=str(cwd) if cwd else None, env=env)
        return proc.wait()
    except FileNotFoundError:
        return 127


def ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def hash_file(p: Path) -> str:
    h = hashlib.sha1()
    with p.open('rb') as f:
        for chunk in iter(lambda: f.read(65536), b''):
            h.update(chunk)
    return h.hexdigest()


def write_text(p: Path, s: str) -> None:
    ensure_dir(p.parent)
    p.write_text(s, encoding='utf-8')


def yaml_dump(d: Dict) -> str:
    if yaml is None:
        # Minimal fallback YAML-ish
        return "\n".join(f"{k}: {v}" for k, v in d.items())
    return yaml.safe_dump(d, sort_keys=False, allow_unicode=True)


def now_stamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

# ------------------------------ Discovery ------------------------------ #

def csc_path_for(ararl_dir: Path, nodes: int, topo_id: str) -> Path:
    """Prefer per-topology CSC; fallback to single CSC if placements absent.
    Allows fallback only when running a single-topology job (topo_id in {"01","1"}).
    """
    p = ararl_dir / f"placements/N{nodes}/simulation-nodes{nodes}-topo{topo_id}.csc"
    if p.is_file():
        return p
    p_single = ararl_dir / f"simulation-nodes{nodes}.csc"
    if p_single.is_file():
        if topo_id not in ("01", "1"):
            die(f"Placements not found for N{nodes} (expected {p}); use --topologies 1 or pre-generate placements.")
        return p_single
    die(f"CSC not found for N{nodes}. Expected {p} or {p_single}")


def pos_header_for(ararl_dir: Path, nodes: int, topo_id: str) -> Path:
    """Prefer per-topology positions header; fallback to single header if placements absent."""
    p = ararl_dir / f"placements/N{nodes}/positions-simulation-nodes{nodes}-topo{topo_id}.h"
    if p.is_file():
        return p
    p_single = ararl_dir / f"positions-simulation-nodes{nodes}.h"
    if p_single.is_file():
        if topo_id not in ("01", "1"):
            die(f"Placements not found for N{nodes} (expected {p}); use --topologies 1 or pre-generate placements.")
        return p_single
    die(f"Positions header not found for N{nodes}. Expected {p} or {p_single}")


def makefile_for_ppm(ararl_dir: Path, ppm: int) -> Path:
    return ararl_dir / f"Makefile-ppm{ppm}"


# ------------------------------ Health checks ------------------------------ #

def basic_log_health(log_path: Path, expected_nodes: int) -> Tuple[bool, Dict[str, int]]:
    """Return (ok, stats). Checks existence, WRAPUP count, SINK_SUMMARY count."""
    stats = {"wrapup": 0, "sink_summary": 0}
    if not log_path.is_file():
        return (False, stats)
    try:
        with log_path.open('r', encoding='utf-8', errors='ignore') as f:
            for line in f:
                if line.startswith("WRAPUP"):
                    stats["wrapup"] += 1
                elif line.startswith("SINK_SUMMARY"):
                    stats["sink_summary"] += 1
    except Exception:
        return (False, stats)
    ok_wrap = (stats["wrapup"] == expected_nodes) or (stats["wrapup"] == expected_nodes - 1)
    ok = ok_wrap and stats["sink_summary"] == 1
    return ok, stats

# ------------------------------ Runner core ------------------------------ #

def run_block(cfg: RunnerConfig, nodes: int, ppm: int, topo_id: str, traffic_seed: int, mask_name: str) -> Tuple[bool, str]:
    """Run one (nodes, ppm, topo, seed, mask). Returns (success, run_dir_str)."""
    # Resolve paths
    ararl_dir = cfg.ararl_dir
    csc_src = csc_path_for(ararl_dir, nodes, topo_id)
    pos_src = pos_header_for(ararl_dir, nodes, topo_id)
    mk_src = makefile_for_ppm(ararl_dir, ppm)
    mask_file = cfg.mask_files.get(mask_name)

    if not csc_src.is_file():
        die(f"CSC not found: {csc_src}")
    if not pos_src.is_file():
        die(f"Positions header not found: {pos_src}")
    if not mk_src.is_file():
        die(f"Makefile for ppm {ppm} not found: {mk_src}")
    if mask_file is None or not mask_file.is_file():
        die(f"Mask file missing for '{mask_name}': {mask_file}")

    # Prepare run dir
    run_dir = cfg.logs_dir / f"N{nodes}_PPM{ppm}" / f"topo{topo_id}" / f"seed{traffic_seed}" / mask_name
    ensure_dir(run_dir)

    # Workspace mutations (sequential v1: operate in-place like the bash script)
    sim_csc = ararl_dir / "simulation.csc"
    pos_hdr = ararl_dir / "positions-simulation.h"
    mk_dst = ararl_dir / "Makefile"

    # Clean previous build artifacts
    for d in (ararl_dir / "rpl", ararl_dir / "build"):
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)

    # Copy scenario assets
    shutil.copy2(csc_src, sim_csc)
    shutil.copy2(pos_src, pos_hdr)
    shutil.copy2(mk_src, mk_dst)

    # Export environment for ScriptRunner/Agent
    env = os.environ.copy()
    env.update({
        "NODES": str(nodes),
        "PPM": str(ppm),
        "TOPOLOGY_ID": topo_id,
        "TRAFFIC_SEED": str(traffic_seed),
        "SIM_SEED": str(cfg.sim_seed),
        "AGENT_SEED": str(cfg.agent_seed),
        "DURATION_SF": str(cfg.duration_sf),
        "WARMUP_SF": str(cfg.warmup_sf),
        "MASK_NAME": mask_name,
        "MASK_FILE": str(mask_file.resolve()),
    })
    if cfg.tx_range is not None:
        env["TX_RANGE"] = str(cfg.tx_range)
    if cfg.int_range is not None:
        env["INT_RANGE"] = str(cfg.int_range)

    csc_path = sim_csc
    gradle_root = cfg.gradle_root

    # Launch (sequential)
    gradlew_path = gradle_root / "gradlew"
    cmd = [str(gradlew_path), "-p", str(gradle_root), "run", "--args", f"--no-gui {csc_path}"]

    # Write run_meta.yaml
    meta = {
        "timestamp": now_stamp(),
        "nodes": nodes,
        "ppm": ppm,
        "topology_id": topo_id,
        "traffic_seed": traffic_seed,
        "sim_seed": cfg.sim_seed,
        "agent_seed": cfg.agent_seed,
        "duration_sf": cfg.duration_sf,
        "warmup_sf": cfg.warmup_sf,
        "mask_name": mask_name,
        "mask_file": str(mask_file),
        "ararl_dir": str(ararl_dir.resolve()),
        "csc_src": str(csc_src),
        "pos_header_src": str(pos_src),
        "ppm_makefile_src": str(mk_src),
        "gradle_root": str(gradle_root.resolve()),
    }
    write_text(run_dir / "run_meta.yaml", yaml_dump(meta))

    if cfg.dry_run:
        print(f"[DRY-RUN] Would execute: {' '.join(cmd)}")
        return True, str(run_dir)

    # Execute Cooja
    exit_code = sh(cmd, env=env)

    # Move logs
    cooja_log = Path.cwd() / "COOJA.testlog"  # Cooja writes here by default
    if not cooja_log.exists():
        print(f"[WARN] COOJA.testlog not found after run: {cooja_log}")
    else:
        shutil.move(str(cooja_log), str(run_dir / "COOJA.testlog"))

    # Also store stdout/stderr if gradle produced them (optional)
    # (Gradle prints to console; capture is handled by the terminal)

    # Health checks
    ok, stats = basic_log_health(run_dir / "COOJA.testlog", expected_nodes=nodes)
    if not ok:
        print(f"[WARN] Health check failed WRAPUP={stats['wrapup']} SINK_SUMMARY={stats['sink_summary']} (expected {nodes}/1)")

    # Cleanup temporary files in ARARL_DIR
    for p in (mk_dst, sim_csc, pos_hdr):
        try:
            if p.exists():
                p.unlink()
        except Exception:
            pass
    for d in (ararl_dir / "rpl", ararl_dir / "build"):
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)

    return ok, str(run_dir)

# ------------------------------ CLI / Main ------------------------------ #

def parse_args() -> RunnerConfig:
    ap = argparse.ArgumentParser(
        description="Run Cooja simulations for (nodes×ppm×topology×seed×mask), sequential v1",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    ap.add_argument("--ararl-dir", type=Path, required=True, help="Path to experiment dir (has placements/, Makefile-ppm*, etc.)")
    ap.add_argument("--logs-dir", type=Path, default=Path("testbed/logs"), help="Base dir for per-run logs")
    ap.add_argument("--gradle-root", type=Path, default=Path("contiki-ng/tools/cooja"), help="Gradle project root for Cooja")
    ap.add_argument("--nodes", type=int, nargs="+", default=[60,80,100])
    ap.add_argument("--ppm", type=int, nargs="+", default=[80,100,120])
    ap.add_argument("--topologies", type=int, default=10, help="Number of topology IDs (01..NN)")
    ap.add_argument("--topology-ids", type=str, nargs="*", help="Explicit topology ids like 01 02 03; overrides --topologies if set")
    ap.add_argument("--masks", type=str, default="baseline:candidate", help="Mask names separated by ':' in run order")
    ap.add_argument("--mask-files", type=str, required=True, help="Mapping name:file pairs separated by commas. Example: baseline:masks/mask-etx.yaml,candidate:masks/mask-x.yaml")
    ap.add_argument("--duration-sf", type=int, default=180)
    ap.add_argument("--warmup-sf", type=int, default=12)
    ap.add_argument("--sim-seed", type=int, default=67890)
    ap.add_argument("--agent-seed", type=int, default=12345)
    ap.add_argument("--traffic-seeds", type=int, nargs="+", default=[1])
    ap.add_argument("--tx-range", type=float, default=None)
    ap.add_argument("--int-range", type=float, default=None)
    ap.add_argument("--dry-run", action="store_true")

    args = ap.parse_args()

    # Build topology id list
    if args.topology_ids:
        topo_ids = args.topology_ids
    else:
        width = len(str(args.topologies))
        topo_ids = [str(i).zfill(width) for i in range(1, args.topologies + 1)]

    # Parse masks and mapping
    mask_names = args.masks.split(":") if args.masks else []
    mask_map: Dict[str, Path] = {}
    for pair in args.mask_files.split(','):
        if not pair.strip():
            continue
        name, file = pair.split(':', 1)
        mask_map[name.strip()] = Path(file.strip())

    # Validate: every mask listed must have a file mapping
    missing = [m for m in mask_names if m not in mask_map]
    if missing:
        die(f"Mask files missing for: {missing}. Provided map: {mask_map}")

    return RunnerConfig(
        ararl_dir=args.ararl_dir.resolve(),
        logs_dir=args.logs_dir.resolve(),
        gradle_root=args.gradle_root.resolve(),
        nodes=args.nodes,
        ppms=args.ppm,
        topology_count=args.topologies,
        topology_ids=topo_ids,
        masks=mask_names,
        mask_files=mask_map,
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

    print("== Cooja Runner v1 ==")
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

    t0 = datetime.now()
    ensure_dir(cfg.logs_dir)

    total = 0
    ok_count = 0

    for n in cfg.nodes:
        for ppm in cfg.ppms:
            for topo in cfg.topology_ids:
                for seed in cfg.traffic_seeds:
                    # Paired run: baseline first (if present), then other masks
                    order = cfg.masks
                    for mask in order:
                        total += 1
                        print("-" * 78)
                        print(f"RUN {total}: N={n} PPM={ppm} topo={topo} seed={seed} mask={mask}")
                        success, rdir = run_block(cfg, n, ppm, topo, seed, mask)
                        print(f"Completed: success={success} → {rdir}")
                        if success:
                            ok_count += 1

    dt = datetime.now() - t0
    print("=" * 78)
    print(f"DONE. Runs attempted: {total}, passed basic health: {ok_count}. Elapsed: {dt}.")


if __name__ == "__main__":
    main()
