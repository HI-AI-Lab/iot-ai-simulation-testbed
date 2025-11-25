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
import csv
import hashlib
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import textwrap
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

try:
    import yaml  # pyyaml
except Exception as e:
    yaml = None

try:
    import csv
except Exception:
    csv = None

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
    Fallback is only allowed for a single-topology job (topo_id in {"01","1"}).
    """
    p = ararl_dir / f"placements/N{nodes}/simulation-nodes{nodes}-topo{topo_id}.csc"
    if p.is_file():
        return p
    p_single = ararl_dir / f"simulation-nodes{nodes}.csc"
    if p_single.is_file():
        if topo_id not in ("01", "1"):
            die(f"Placements not found for N{nodes} (expected {p}); use --topologies 1/--topology-ids 01 or pre-generate placements.")
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
            die(f"Placements not found for N{nodes} (expected {p}); use --topologies 1/--topology-ids 01 or pre-generate placements.")
        return p_single
    die(f"Positions header not found for N{nodes}. Expected {p} or {p_single}")

def makefile_for_ppm(ararl_dir: Path, ppm: int) -> Path:
    return ararl_dir / f"Makefile-ppm{ppm}"


# ------------------------------ Log Parsing ------------------------------ #

@dataclass
class RunMetrics:
    """Metrics extracted from a single simulation run."""
    e2e_latency: List[float] = field(default_factory=list)  # ms, per-node AvgE2E
    nlt: Optional[float] = None  # ms, network lifetime (first node death time)
    qlr: List[float] = field(default_factory=list)  # queue loss ratio per node
    prr: Optional[float] = None  # packet reception ratio (network-wide)
    
    # Raw data for debugging
    total_gen: int = 0
    total_recv: int = 0
    total_qloss: int = 0
    node_count: int = 0


def parse_log(log_path: Path) -> Optional[RunMetrics]:
    """Parse simulation log and extract E2E, NLT, QLR, PRR metrics."""
    if not log_path.is_file():
        return None
    
    metrics = RunMetrics()
    node_data: Dict[int, Dict] = {}  # node_id -> {gen, qloss, end_ms, reason, recv, e2e}
    first_death_ms: Optional[int] = None
    
    try:
        with log_path.open('r', encoding='utf-8', errors='ignore') as f:
            for line in f:
                # Parse WRAPUP lines from nodes
                # Format: WRAPUP node_id=<id> reason=<reason> end_ms=<ms> Gen=<count> Fwd=<count> QLoss=<count> ...
                if line.startswith("WRAPUP node_id="):
                    match = re.search(r'node_id=(\d+)', line)
                    if match:
                        node_id = int(match.group(1))
                        if node_id == 1:  # Skip sink
                            continue
                        
                        if node_id not in node_data:
                            node_data[node_id] = {}
                        
                        # Extract end_ms
                        match_ms = re.search(r'end_ms=(\d+)', line)
                        if match_ms:
                            end_ms = int(match_ms.group(1))
                            node_data[node_id]['end_ms'] = end_ms
                        
                        # Extract reason
                        match_reason = re.search(r'reason=(\S+)', line)
                        if match_reason:
                            reason = match_reason.group(1)
                            node_data[node_id]['reason'] = reason
                            # Track first energy death for NLT
                            if reason == 'END_ENERGY' and (first_death_ms is None or end_ms < first_death_ms):
                                first_death_ms = end_ms
                        
                        # Extract Gen
                        match_gen = re.search(r'Gen=(\d+)', line)
                        if match_gen:
                            node_data[node_id]['gen'] = int(match_gen.group(1))
                            metrics.total_gen += int(match_gen.group(1))
                        
                        # Extract QLoss
                        match_qloss = re.search(r'QLoss=(\d+)', line)
                        if match_qloss:
                            node_data[node_id]['qloss'] = int(match_qloss.group(1))
                            metrics.total_qloss += int(match_qloss.group(1))
                
                # Parse SINK_SUMMARY lines
                # Format: SINK_SUMMARY node=<id> Recv=<count> AvgE2E=<ms> MinE2E=<ms> MaxE2E=<ms>
                # Or: SINK_SUMMARY node=<id> Recv=0
                elif line.startswith("SINK_SUMMARY node="):
                    match = re.search(r'node=(\d+)', line)
                    if match:
                        node_id = int(match.group(1))
                        if node_id == 1:  # Skip sink
                            continue
                        
                        if node_id not in node_data:
                            node_data[node_id] = {}
                        
                        # Extract Recv
                        match_recv = re.search(r'Recv=(\d+)', line)
                        if match_recv:
                            recv = int(match_recv.group(1))
                            node_data[node_id]['recv'] = recv
                            metrics.total_recv += recv
                            
                            # Extract AvgE2E if available
                            match_e2e = re.search(r'AvgE2E=(\d+)ms', line)
                            if match_e2e:
                                e2e = float(match_e2e.group(1))
                                node_data[node_id]['e2e'] = e2e
                                metrics.e2e_latency.append(e2e)
    
    except Exception as e:
        print(f"[WARN] Failed to parse log {log_path}: {e}")
        return None
    
    # Calculate metrics
    metrics.node_count = len(node_data)
    
    # NLT: First node death time, or simulation end if no deaths
    if first_death_ms is not None:
        metrics.nlt = float(first_death_ms)
    elif node_data:
        # Use max end_ms as fallback (simulation end time)
        max_end = max((n.get('end_ms', 0) for n in node_data.values()), default=0)
        if max_end > 0:
            metrics.nlt = float(max_end)
    
    # QLR: Queue Loss Ratio per node (QLoss / Gen)
    for node_id, data in node_data.items():
        gen = data.get('gen', 0)
        qloss = data.get('qloss', 0)
        if gen > 0:
            qlr = qloss / gen
            metrics.qlr.append(qlr)
    
    # PRR: Packet Reception Ratio (total received / total generated)
    if metrics.total_gen > 0:
        metrics.prr = metrics.total_recv / metrics.total_gen
    
    return metrics


@dataclass
class AggregatedMetrics:
    """Aggregated statistics across multiple runs."""
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
    """Aggregate metrics across multiple runs."""
    agg = AggregatedMetrics()
    agg.run_count = len(metrics_list)
    agg.valid_runs = len([m for m in metrics_list if m is not None])
    
    if agg.valid_runs == 0:
        return agg
    
    # Collect all values
    e2e_values: List[float] = []
    nlt_values: List[float] = []
    qlr_values: List[float] = []
    prr_values: List[float] = []
    
    for m in metrics_list:
        if m is None:
            continue
        
        # E2E: collect all per-node latencies
        e2e_values.extend(m.e2e_latency)
        
        # NLT: collect network lifetime
        if m.nlt is not None:
            nlt_values.append(m.nlt)
        
        # QLR: collect all per-node ratios
        qlr_values.extend(m.qlr)
        
        # PRR: collect network-wide ratio
        if m.prr is not None:
            prr_values.append(m.prr)
    
    # Calculate statistics
    if e2e_values:
        agg.e2e_mean = statistics.mean(e2e_values)
        agg.e2e_std = statistics.stdev(e2e_values) if len(e2e_values) > 1 else 0.0
        agg.e2e_min = min(e2e_values)
        agg.e2e_max = max(e2e_values)
    
    if nlt_values:
        agg.nlt_mean = statistics.mean(nlt_values)
        agg.nlt_std = statistics.stdev(nlt_values) if len(nlt_values) > 1 else 0.0
        agg.nlt_min = min(nlt_values)
        agg.nlt_max = max(nlt_values)
    
    if qlr_values:
        agg.qlr_mean = statistics.mean(qlr_values)
        agg.qlr_std = statistics.stdev(qlr_values) if len(qlr_values) > 1 else 0.0
        agg.qlr_min = min(qlr_values)
        agg.qlr_max = max(qlr_values)
    
    if prr_values:
        agg.prr_mean = statistics.mean(prr_values)
        agg.prr_std = statistics.stdev(prr_values) if len(prr_values) > 1 else 0.0
        agg.prr_min = min(prr_values)
        agg.prr_max = max(prr_values)
    
    return agg


def write_aggregated_csv(agg: AggregatedMetrics, output_path: Path, mask_name: str, nodes: int, ppm: int) -> None:
    """Write aggregated metrics to CSV file."""
    ensure_dir(output_path.parent)
    
    with output_path.open('w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['Metric', 'Mean', 'Std', 'Min', 'Max', 'Unit'])
        
        if agg.e2e_mean is not None:
            writer.writerow(['E2E', f'{agg.e2e_mean:.2f}', f'{agg.e2e_std:.2f}', 
                           f'{agg.e2e_min:.2f}', f'{agg.e2e_max:.2f}', 'ms'])
        
        if agg.nlt_mean is not None:
            writer.writerow(['NLT', f'{agg.nlt_mean:.2f}', f'{agg.nlt_std:.2f}', 
                           f'{agg.nlt_min:.2f}', f'{agg.nlt_max:.2f}', 'ms'])
        
        if agg.qlr_mean is not None:
            writer.writerow(['QLR', f'{agg.qlr_mean:.4f}', f'{agg.qlr_std:.4f}', 
                           f'{agg.qlr_min:.4f}', f'{agg.qlr_max:.4f}', 'ratio'])
        
        if agg.prr_mean is not None:
            writer.writerow(['PRR', f'{agg.prr_mean:.4f}', f'{agg.prr_std:.4f}', 
                           f'{agg.prr_min:.4f}', f'{agg.prr_max:.4f}', 'ratio'])
        
        writer.writerow([])
        writer.writerow(['Runs', agg.run_count, 'Valid', agg.valid_runs, '', ''])


def write_aggregated_json(agg: AggregatedMetrics, output_path: Path, mask_name: str, nodes: int, ppm: int) -> None:
    """Write aggregated metrics to JSON file."""
    ensure_dir(output_path.parent)
    
    data = {
        'mask': mask_name,
        'nodes': nodes,
        'ppm': ppm,
        'runs': {
            'total': agg.run_count,
            'valid': agg.valid_runs
        },
        'metrics': {}
    }
    
    if agg.e2e_mean is not None:
        data['metrics']['E2E'] = {
            'mean': agg.e2e_mean,
            'std': agg.e2e_std,
            'min': agg.e2e_min,
            'max': agg.e2e_max,
            'unit': 'ms'
        }
    
    if agg.nlt_mean is not None:
        data['metrics']['NLT'] = {
            'mean': agg.nlt_mean,
            'std': agg.nlt_std,
            'min': agg.nlt_min,
            'max': agg.nlt_max,
            'unit': 'ms'
        }
    
    if agg.qlr_mean is not None:
        data['metrics']['QLR'] = {
            'mean': agg.qlr_mean,
            'std': agg.qlr_std,
            'min': agg.qlr_min,
            'max': agg.qlr_max,
            'unit': 'ratio'
        }
    
    if agg.prr_mean is not None:
        data['metrics']['PRR'] = {
            'mean': agg.prr_mean,
            'std': agg.prr_std,
            'min': agg.prr_min,
            'max': agg.prr_max,
            'unit': 'ratio'
        }
    
    output_path.write_text(json.dumps(data, indent=2), encoding='utf-8')


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

# ------------------------------ Log Parsing ------------------------------ #

@dataclass
class RunMetrics:
    """Metrics extracted from a single simulation run."""
    e2e_latency: List[float] = field(default_factory=list)  # ms, per-node AvgE2E
    nlt: Optional[float] = None  # ms, network lifetime (first node death time)
    qlr: List[float] = field(default_factory=list)  # queue loss ratio per node
    prr: Optional[float] = None  # packet reception ratio (network-wide)
    
    # Raw data for debugging
    total_gen: int = 0
    total_recv: int = 0
    total_qloss: int = 0
    node_count: int = 0


def parse_log(log_path: Path) -> Optional[RunMetrics]:
    """Parse simulation log and extract E2E, NLT, QLR, PRR metrics."""
    if not log_path.is_file():
        return None
    
    metrics = RunMetrics()
    node_data: Dict[int, Dict] = {}  # node_id -> {gen, qloss, end_ms, reason, recv, e2e}
    first_death_ms: Optional[int] = None
    
    try:
        with log_path.open('r', encoding='utf-8', errors='ignore') as f:
            for line in f:
                # Parse WRAPUP lines from nodes
                # Format: WRAPUP node_id=<id> reason=<reason> end_ms=<ms> Gen=<count> Fwd=<count> QLoss=<count> ...
                if line.startswith("WRAPUP node_id="):
                    match = re.search(r'node_id=(\d+)', line)
                    if match:
                        node_id = int(match.group(1))
                        if node_id == 1:  # Skip sink
                            continue
                        
                        if node_id not in node_data:
                            node_data[node_id] = {}
                        
                        # Extract end_ms
                        match_ms = re.search(r'end_ms=(\d+)', line)
                        if match_ms:
                            end_ms = int(match_ms.group(1))
                            node_data[node_id]['end_ms'] = end_ms
                        
                        # Extract reason
                        match_reason = re.search(r'reason=(\S+)', line)
                        if match_reason:
                            reason = match_reason.group(1)
                            node_data[node_id]['reason'] = reason
                            # Track first energy death for NLT
                            if reason == 'END_ENERGY' and (first_death_ms is None or end_ms < first_death_ms):
                                first_death_ms = end_ms
                        
                        # Extract Gen
                        match_gen = re.search(r'Gen=(\d+)', line)
                        if match_gen:
                            node_data[node_id]['gen'] = int(match_gen.group(1))
                            metrics.total_gen += int(match_gen.group(1))
                        
                        # Extract QLoss
                        match_qloss = re.search(r'QLoss=(\d+)', line)
                        if match_qloss:
                            node_data[node_id]['qloss'] = int(match_qloss.group(1))
                            metrics.total_qloss += int(match_qloss.group(1))
                
                # Parse SINK_SUMMARY lines
                # Format: SINK_SUMMARY node=<id> Recv=<count> AvgE2E=<ms> MinE2E=<ms> MaxE2E=<ms>
                # Or: SINK_SUMMARY node=<id> Recv=0
                elif line.startswith("SINK_SUMMARY node="):
                    match = re.search(r'node=(\d+)', line)
                    if match:
                        node_id = int(match.group(1))
                        if node_id == 1:  # Skip sink
                            continue
                        
                        if node_id not in node_data:
                            node_data[node_id] = {}
                        
                        # Extract Recv
                        match_recv = re.search(r'Recv=(\d+)', line)
                        if match_recv:
                            recv = int(match_recv.group(1))
                            node_data[node_id]['recv'] = recv
                            metrics.total_recv += recv
                            
                            # Extract AvgE2E if available
                            match_e2e = re.search(r'AvgE2E=(\d+)ms', line)
                            if match_e2e:
                                e2e = float(match_e2e.group(1))
                                node_data[node_id]['e2e'] = e2e
                                metrics.e2e_latency.append(e2e)
    
    except Exception as e:
        print(f"[WARN] Failed to parse log {log_path}: {e}")
        return None
    
    # Calculate metrics
    metrics.node_count = len(node_data)
    
    # NLT: First node death time, or simulation end if no deaths
    if first_death_ms is not None:
        metrics.nlt = float(first_death_ms)
    elif node_data:
        # Use max end_ms as fallback (simulation end time)
        max_end = max((n.get('end_ms', 0) for n in node_data.values()), default=0)
        if max_end > 0:
            metrics.nlt = float(max_end)
    
    # QLR: Queue Loss Ratio per node (QLoss / Gen)
    for node_id, data in node_data.items():
        gen = data.get('gen', 0)
        qloss = data.get('qloss', 0)
        if gen > 0:
            qlr = qloss / gen
            metrics.qlr.append(qlr)
    
    # PRR: Packet Reception Ratio (total received / total generated)
    if metrics.total_gen > 0:
        metrics.prr = metrics.total_recv / metrics.total_gen
    
    return metrics


@dataclass
class AggregatedMetrics:
    """Aggregated statistics across multiple runs."""
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
    """Aggregate metrics across multiple runs."""
    agg = AggregatedMetrics()
    agg.run_count = len(metrics_list)
    agg.valid_runs = len([m for m in metrics_list if m is not None])
    
    if agg.valid_runs == 0:
        return agg
    
    # Collect all values
    e2e_values: List[float] = []
    nlt_values: List[float] = []
    qlr_values: List[float] = []
    prr_values: List[float] = []
    
    for m in metrics_list:
        if m is None:
            continue
        
        # E2E: collect all per-node latencies
        e2e_values.extend(m.e2e_latency)
        
        # NLT: collect network lifetime
        if m.nlt is not None:
            nlt_values.append(m.nlt)
        
        # QLR: collect all per-node ratios
        qlr_values.extend(m.qlr)
        
        # PRR: collect network-wide ratio
        if m.prr is not None:
            prr_values.append(m.prr)
    
    # Calculate statistics
    if e2e_values:
        agg.e2e_mean = statistics.mean(e2e_values)
        agg.e2e_std = statistics.stdev(e2e_values) if len(e2e_values) > 1 else 0.0
        agg.e2e_min = min(e2e_values)
        agg.e2e_max = max(e2e_values)
    
    if nlt_values:
        agg.nlt_mean = statistics.mean(nlt_values)
        agg.nlt_std = statistics.stdev(nlt_values) if len(nlt_values) > 1 else 0.0
        agg.nlt_min = min(nlt_values)
        agg.nlt_max = max(nlt_values)
    
    if qlr_values:
        agg.qlr_mean = statistics.mean(qlr_values)
        agg.qlr_std = statistics.stdev(qlr_values) if len(qlr_values) > 1 else 0.0
        agg.qlr_min = min(qlr_values)
        agg.qlr_max = max(qlr_values)
    
    if prr_values:
        agg.prr_mean = statistics.mean(prr_values)
        agg.prr_std = statistics.stdev(prr_values) if len(prr_values) > 1 else 0.0
        agg.prr_min = min(prr_values)
        agg.prr_max = max(prr_values)
    
    return agg


def write_aggregated_csv(agg: AggregatedMetrics, output_path: Path, mask_name: str, nodes: int, ppm: int) -> None:
    """Write aggregated metrics to CSV file."""
    ensure_dir(output_path.parent)
    
    with output_path.open('w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['Metric', 'Mean', 'Std', 'Min', 'Max', 'Unit'])
        
        if agg.e2e_mean is not None:
            writer.writerow(['E2E', f'{agg.e2e_mean:.2f}', f'{agg.e2e_std:.2f}', 
                           f'{agg.e2e_min:.2f}', f'{agg.e2e_max:.2f}', 'ms'])
        
        if agg.nlt_mean is not None:
            writer.writerow(['NLT', f'{agg.nlt_mean:.2f}', f'{agg.nlt_std:.2f}', 
                           f'{agg.nlt_min:.2f}', f'{agg.nlt_max:.2f}', 'ms'])
        
        if agg.qlr_mean is not None:
            writer.writerow(['QLR', f'{agg.qlr_mean:.4f}', f'{agg.qlr_std:.4f}', 
                           f'{agg.qlr_min:.4f}', f'{agg.qlr_max:.4f}', 'ratio'])
        
        if agg.prr_mean is not None:
            writer.writerow(['PRR', f'{agg.prr_mean:.4f}', f'{agg.prr_std:.4f}', 
                           f'{agg.prr_min:.4f}', f'{agg.prr_max:.4f}', 'ratio'])
        
        writer.writerow([])
        writer.writerow(['Runs', agg.run_count, 'Valid', agg.valid_runs, '', ''])


def write_aggregated_json(agg: AggregatedMetrics, output_path: Path, mask_name: str, nodes: int, ppm: int) -> None:
    """Write aggregated metrics to JSON file."""
    ensure_dir(output_path.parent)
    
    data = {
        'mask': mask_name,
        'nodes': nodes,
        'ppm': ppm,
        'runs': {
            'total': agg.run_count,
            'valid': agg.valid_runs
        },
        'metrics': {}
    }
    
    if agg.e2e_mean is not None:
        data['metrics']['E2E'] = {
            'mean': agg.e2e_mean,
            'std': agg.e2e_std,
            'min': agg.e2e_min,
            'max': agg.e2e_max,
            'unit': 'ms'
        }
    
    if agg.nlt_mean is not None:
        data['metrics']['NLT'] = {
            'mean': agg.nlt_mean,
            'std': agg.nlt_std,
            'min': agg.nlt_min,
            'max': agg.nlt_max,
            'unit': 'ms'
        }
    
    if agg.qlr_mean is not None:
        data['metrics']['QLR'] = {
            'mean': agg.qlr_mean,
            'std': agg.qlr_std,
            'min': agg.qlr_min,
            'max': agg.qlr_max,
            'unit': 'ratio'
        }
    
    if agg.prr_mean is not None:
        data['metrics']['PRR'] = {
            'mean': agg.prr_mean,
            'std': agg.prr_std,
            'min': agg.prr_min,
            'max': agg.prr_max,
            'unit': 'ratio'
        }
    
    output_path.write_text(json.dumps(data, indent=2), encoding='utf-8')

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
    
    # Collect metrics per mask for aggregation
    mask_metrics: Dict[Tuple[int, int, str], Dict[str, List[RunMetrics]]] = {}
    # Structure: (nodes, ppm, mask) -> {topo: [metrics...]}

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
                            
                            # Parse log and collect metrics
                            log_path = Path(rdir) / "COOJA.testlog"
                            metrics = parse_log(log_path)
                            if metrics:
                                key = (n, ppm, mask)
                                if key not in mask_metrics:
                                    mask_metrics[key] = {}
                                if topo not in mask_metrics[key]:
                                    mask_metrics[key][topo] = []
                                mask_metrics[key][topo].append(metrics)
                            else:
                                print(f"[WARN] Failed to parse metrics from {log_path}")

    # Aggregate and write results per mask
    print("\n" + "=" * 78)
    print("AGGREGATING RESULTS...")
    print("=" * 78)
    
    for (n, ppm, mask), topo_data in mask_metrics.items():
        # Collect all metrics across topologies and seeds
        all_metrics: List[RunMetrics] = []
        for topo, metrics_list in topo_data.items():
            all_metrics.extend(metrics_list)
        
        if not all_metrics:
            print(f"[WARN] No valid metrics for N={n} PPM={ppm} mask={mask}")
            continue
        
        # Aggregate
        agg = aggregate_metrics(all_metrics)
        
        # Write results
        base_dir = cfg.logs_dir / f"N{n}_PPM{ppm}" / mask
        ensure_dir(base_dir)
        
        csv_path = base_dir / "aggregated_results.csv"
        json_path = base_dir / "aggregated_results.json"
        
        write_aggregated_csv(agg, csv_path, mask, n, ppm)
        write_aggregated_json(agg, json_path, mask, n, ppm)
        
        # Print summary
        print(f"\nMask: {mask} | Nodes: {n} | PPM: {ppm}")
        print(f"  Runs: {agg.valid_runs}/{agg.run_count} valid")
        if agg.e2e_mean is not None:
            print(f"  E2E:  {agg.e2e_mean:.2f} ± {agg.e2e_std:.2f} ms (min={agg.e2e_min:.2f}, max={agg.e2e_max:.2f})")
        if agg.nlt_mean is not None:
            print(f"  NLT:  {agg.nlt_mean:.2f} ± {agg.nlt_std:.2f} ms (min={agg.nlt_min:.2f}, max={agg.nlt_max:.2f})")
        if agg.qlr_mean is not None:
            print(f"  QLR:  {agg.qlr_mean:.4f} ± {agg.qlr_std:.4f} (min={agg.qlr_min:.4f}, max={agg.qlr_max:.4f})")
        if agg.prr_mean is not None:
            print(f"  PRR:  {agg.prr_mean:.4f} ± {agg.prr_std:.4f} (min={agg.prr_min:.4f}, max={agg.prr_max:.4f})")
        print(f"  Results saved to: {base_dir}")

    dt = datetime.now() - t0
    print("\n" + "=" * 78)
    print(f"DONE. Runs attempted: {total}, passed basic health: {ok_count}. Elapsed: {dt}.")


if __name__ == "__main__":
    main()
