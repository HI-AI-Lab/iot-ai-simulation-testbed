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
from typing import Any, Dict, List, Optional, Set, Tuple
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
import time, selectors

try:
    import yaml  # optional, for nicer meta YAML
except Exception:
    yaml = None

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
class MaskSpec:
    name: str
    file: Path

@dataclass
class RunnerConfig:
    ararl_dir: Path
    logs_dir: Path
    gradle_root: Path
    nodes: List[int]
    ppms: List[int]
    topology_ids: List[str]
    masks: List[MaskSpec]
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
    resume: bool
    error_log_tail: int
    heartbeat_secs: int

SummaryKey = Tuple[int, int, str, int, str]
TaskKey = Tuple[str, int, int, str, int]
CHECKPOINT_VERSION = 2
SUPPORTED_CHECKPOINT_VERSIONS = {1, CHECKPOINT_VERSION}

# ------------------------------ Helpers ------------------------------ #

def die(msg: str, code: int = 2) -> None:
    print(f"[FATAL] {msg}", file=sys.stderr); sys.exit(code)

def dedupe_preserve_order(values: List[Any]) -> List[Any]:
    seen: Set[Any] = set()
    out: List[Any] = []
    for v in values:
        if v in seen:
            continue
        seen.add(v)
        out.append(v)
    return out

def sh(
    cmd: List[str],
    cwd: Optional[Path] = None,
    env: Optional[Dict[str, str]] = None,
    out_path: Optional[Path] = None,
) -> int:
    try:
        if out_path is None:
            proc = subprocess.Popen(cmd, cwd=str(cwd) if cwd else None, env=env)
            return proc.wait()

        ensure_dir(out_path.parent)

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

def fmt_hms(total_seconds: int) -> str:
    total_seconds = max(0, int(total_seconds))
    hh, rem = divmod(total_seconds, 3600)
    mm, ss = divmod(rem, 60)
    return f"{hh:02d}:{mm:02d}:{ss:02d}"

def write_json_atomic(path: Path, payload: Dict[str, Any]) -> None:
    ensure_dir(path.parent)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(path)

def run_dir_for(logs_dir: Path, mask_name: str, n: int, ppm: int, topo: str, seed: int) -> Path:
    return logs_dir / f"N{n}_PPM{ppm}" / f"topo{topo}" / f"seed{seed}" / mask_name

def _summary_key(n: int, ppm: int, topo: str, seed: int, mask: str) -> SummaryKey:
    return (n, ppm, topo, seed, mask)

def checkpoint_path_for(logs_dir: Path) -> Path:
    return logs_dir / "run_checkpoint.json"

def task_id(mask_name: str, n: int, ppm: int, topo: str, seed: int) -> str:
    return f"{mask_name}|{n}|{ppm}|{topo}|{seed}"

def config_to_dict(cfg: RunnerConfig) -> Dict[str, Any]:
    return {
        "ararl_dir": str(cfg.ararl_dir),
        "logs_dir": str(cfg.logs_dir),
        "gradle_root": str(cfg.gradle_root),
        "nodes": cfg.nodes,
        "ppms": cfg.ppms,
        "topology_ids": cfg.topology_ids,
        "masks": [{"name": m.name, "file": str(m.file)} for m in cfg.masks],
        "duration_sf": cfg.duration_sf,
        "warmup_sf": cfg.warmup_sf,
        "sim_seed": cfg.sim_seed,
        "agent_seed": cfg.agent_seed,
        "traffic_seeds": cfg.traffic_seeds,
        "tx_range": cfg.tx_range,
        "int_range": cfg.int_range,
        "gradle_user_home": str(cfg.gradle_user_home) if cfg.gradle_user_home is not None else None,
        "jobs": cfg.jobs,
        "work_root": str(cfg.work_root),
        "keep_work": cfg.keep_work,
        "dry_run": cfg.dry_run,
        "resume": cfg.resume,
        "error_log_tail": cfg.error_log_tail,
        "heartbeat_secs": cfg.heartbeat_secs,
    }

def config_from_dict(d: Dict[str, Any]) -> RunnerConfig:
    def _required(key: str) -> Any:
        if key not in d:
            raise ValueError(f"Checkpoint config missing '{key}'.")
        return d[key]

    masks_raw = d.get("masks", [])
    masks: List[MaskSpec] = []
    for item in masks_raw:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        f = item.get("file")
        if not name or not f:
            continue
        masks.append(MaskSpec(name=name, file=Path(str(f)).resolve()))
    if not masks:
        legacy_name = str(d.get("mask_name", "")).strip()
        legacy_file = d.get("mask_file")
        if legacy_name and legacy_file:
            masks.append(MaskSpec(name=legacy_name, file=Path(str(legacy_file)).resolve()))
    if not masks:
        raise ValueError("Checkpoint has no valid masks configuration.")
    if len({m.name for m in masks}) != len(masks):
        raise ValueError("Checkpoint has duplicate mask names.")

    gradle_user_home_val = d.get("gradle_user_home")
    nodes = dedupe_preserve_order([int(x) for x in d.get("nodes", [])])
    ppms = dedupe_preserve_order([int(x) for x in d.get("ppms", [])])
    if not nodes or any(x < 1 for x in nodes):
        raise ValueError("Checkpoint has invalid nodes list.")
    if not ppms or any(x < 1 for x in ppms):
        raise ValueError("Checkpoint has invalid ppm list.")

    topology_ids_raw = d.get("topology_ids")
    if topology_ids_raw is not None:
        if isinstance(topology_ids_raw, str):
            topology_ids = [topology_ids_raw]
        elif isinstance(topology_ids_raw, list):
            topology_ids = [str(x) for x in topology_ids_raw]
        else:
            raise ValueError("Checkpoint has invalid topology_ids type.")
        topology_ids = dedupe_preserve_order([x.strip() for x in topology_ids if str(x).strip()])
    else:
        topo_count_raw = d.get("topologies")
        try:
            topo_count = int(topo_count_raw)
        except Exception:
            topo_count = 0
        if topo_count < 1:
            raise ValueError("Checkpoint has no topology_ids/topologies configuration.")
        width = max(2, len(str(topo_count)))
        topology_ids = [str(i).zfill(width) for i in range(1, topo_count + 1)]
    if not topology_ids:
        raise ValueError("Checkpoint has empty topology_ids.")

    traffic_seeds_raw = d.get("traffic_seeds")
    if traffic_seeds_raw is None:
        if d.get("traffic_seed") is not None:
            traffic_seeds_raw = [d.get("traffic_seed")]
        elif d.get("seed_count") is not None:
            try:
                seed_count = int(d.get("seed_count"))
            except Exception:
                seed_count = 0
            traffic_seeds_raw = list(range(1, seed_count + 1)) if seed_count > 0 else []
        else:
            traffic_seeds_raw = [1]
    if isinstance(traffic_seeds_raw, list):
        traffic_seeds = dedupe_preserve_order([int(x) for x in traffic_seeds_raw])
    else:
        traffic_seeds = [int(traffic_seeds_raw)]
    if not traffic_seeds or any(s < 1 for s in traffic_seeds):
        raise ValueError("Checkpoint has invalid traffic_seeds configuration.")
    return RunnerConfig(
        ararl_dir=Path(str(_required("ararl_dir"))).resolve(),
        logs_dir=Path(str(_required("logs_dir"))).resolve(),
        gradle_root=Path(str(_required("gradle_root"))).resolve(),
        nodes=nodes,
        ppms=ppms,
        topology_ids=topology_ids,
        masks=masks,
        duration_sf=int(_required("duration_sf")),
        warmup_sf=int(_required("warmup_sf")),
        sim_seed=int(_required("sim_seed")),
        agent_seed=int(_required("agent_seed")),
        traffic_seeds=traffic_seeds,
        tx_range=(float(d["tx_range"]) if d.get("tx_range") is not None else None),
        int_range=(float(d["int_range"]) if d.get("int_range") is not None else None),
        gradle_user_home=(Path(str(gradle_user_home_val)).resolve() if gradle_user_home_val else None),
        jobs=max(1, int(d.get("jobs", 1))),
        work_root=Path(str(d.get("work_root", "testbed/_work"))).resolve(),
        keep_work=bool(d.get("keep_work", False)),
        dry_run=bool(d.get("dry_run", False)),
        resume=True,
        error_log_tail=max(0, int(d.get("error_log_tail", 50))),
        heartbeat_secs=max(0, int(d.get("heartbeat_secs", 60))),
    )

def build_tasks(cfg: RunnerConfig) -> List[TaskKey]:
    seen: Set[TaskKey] = set()
    tasks: List[TaskKey] = []
    for mask in cfg.masks:
        for n in cfg.nodes:
            for ppm in cfg.ppms:
                for topo in cfg.topology_ids:
                    for seed in cfg.traffic_seeds:
                        t = (mask.name, n, ppm, topo, seed)
                        if t in seen:
                            continue
                        seen.add(t)
                        tasks.append(t)
    return tasks

def inspect_checkpoint(path: Path) -> Tuple[str, Optional[Dict[str, Any]], Optional[str]]:
    """
    Returns (status, checkpoint, reason):
      - status="ok": checkpoint is valid and returned
      - status="missing": file does not exist
      - status="invalid": file exists but is malformed/incompatible
    """
    if not path.is_file():
        return ("missing", None, None)
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception as e:
        return ("invalid", None, f"failed to parse JSON: {e}")
    if not isinstance(raw, dict):
        return ("invalid", None, "root must be a JSON object")
    try:
        version = int(raw.get("version", -1))
    except Exception:
        return ("invalid", None, f"invalid version value: {raw.get('version')!r}")
    if version not in SUPPORTED_CHECKPOINT_VERSIONS:
        supported = ", ".join(str(v) for v in sorted(SUPPORTED_CHECKPOINT_VERSIONS))
        return ("invalid", None, f"unsupported version {version}; supported: {supported}")
    if not isinstance(raw.get("config"), dict):
        return ("invalid", None, "missing or invalid 'config' object")
    if not isinstance(raw.get("task_state"), dict):
        return ("invalid", None, "missing or invalid 'task_state' object")
    return ("ok", raw, None)

def init_checkpoint(cfg: RunnerConfig, tasks: List[TaskKey]) -> Dict[str, Any]:
    state: Dict[str, Any] = {}
    for (mask_name, n, ppm, topo, seed) in tasks:
        tid = task_id(mask_name, n, ppm, topo, seed)
        state[tid] = {
            "status": "pending",
            "ok": None,
            "run_dir": str(run_dir_for(cfg.logs_dir, mask_name, n, ppm, topo, seed)),
            "updated_at": now_stamp(),
        }
    return {
        "version": CHECKPOINT_VERSION,
        "created_at": now_stamp(),
        "updated_at": now_stamp(),
        "config": config_to_dict(cfg),
        "task_state": state,
    }

def save_checkpoint(path: Path, checkpoint: Dict[str, Any]) -> None:
    checkpoint["updated_at"] = now_stamp()
    write_json_atomic(path, checkpoint)

def set_task_state(
    checkpoint: Dict[str, Any],
    task: TaskKey,
    status: str,
    ok: Optional[bool] = None,
    run_dir: Optional[str] = None,
) -> None:
    (mask_name, n, ppm, topo, seed) = task
    tid = task_id(mask_name, n, ppm, topo, seed)
    task_state = checkpoint.setdefault("task_state", {})
    entry = task_state.get(tid, {})
    if not isinstance(entry, dict):
        entry = {}
    entry["status"] = status
    if ok is not None:
        entry["ok"] = bool(ok)
    if run_dir is not None:
        entry["run_dir"] = run_dir
    elif "run_dir" not in entry:
        cfg_d = checkpoint.get("config", {})
        logs_dir = Path(str(cfg_d.get("logs_dir", ".")))
        entry["run_dir"] = str(run_dir_for(logs_dir, mask_name, n, ppm, topo, seed))
    entry["updated_at"] = now_stamp()
    task_state[tid] = entry

def load_run_meta(meta_path: Path) -> Optional[Dict[str, Any]]:
    if not meta_path.is_file():
        return None
    text = meta_path.read_text(encoding="utf-8", errors="ignore")
    if yaml is not None:
        try:
            d = yaml.safe_load(text)
            if isinstance(d, dict):
                return d
        except Exception:
            return None
    out: Dict[str, Any] = {}
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        k, v = [x.strip() for x in line.split(":", 1)]
        if not k:
            continue
        out[k] = v
    return out if out else None

def _eq_path(a: Any, b: Path) -> bool:
    if a is None:
        return False
    try:
        return Path(str(a)).resolve() == b.resolve()
    except Exception:
        return False

def _to_int(v: Any) -> Optional[int]:
    try:
        return int(v)
    except Exception:
        return None

def _to_float(v: Any) -> Optional[float]:
    try:
        return float(v)
    except Exception:
        return None

def run_meta_matches(cfg: RunnerConfig, mask: MaskSpec, n: int, ppm: int, topo: str, seed: int, meta: Dict[str, Any]) -> bool:
    if _to_int(meta.get("nodes")) != n:
        return False
    if _to_int(meta.get("ppm")) != ppm:
        return False
    if str(meta.get("topology_id")) != str(topo):
        return False
    if _to_int(meta.get("traffic_seed")) != seed:
        return False
    if _to_int(meta.get("sim_seed")) != cfg.sim_seed:
        return False
    if _to_int(meta.get("agent_seed")) != cfg.agent_seed:
        return False
    if _to_int(meta.get("duration_sf")) != cfg.duration_sf:
        return False
    if _to_int(meta.get("warmup_sf")) != cfg.warmup_sf:
        return False
    if str(meta.get("mask_name")) != mask.name:
        return False
    if not _eq_path(meta.get("mask_file"), mask.file):
        return False

    has_tx = "tx_range" in meta
    has_int = "int_range" in meta
    if not has_tx and not has_int:
        # Backward compatibility for older run_meta.yaml files (pre-range fields).
        # These are only compatible with default (unset) ranges.
        return cfg.tx_range is None and cfg.int_range is None
    if has_tx != has_int:
        return False

    tx_m = _to_float(meta.get("tx_range"))
    int_m = _to_float(meta.get("int_range"))
    if (cfg.tx_range is None) != (tx_m is None):
        return False
    if (cfg.int_range is None) != (int_m is None):
        return False
    if cfg.tx_range is not None and tx_m is not None and abs(cfg.tx_range - tx_m) > 1e-12:
        return False
    if cfg.int_range is not None and int_m is not None and abs(cfg.int_range - int_m) > 1e-12:
        return False
    return True

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

def auto_jobs_from_cores(fraction: float = 0.8) -> int:
    cores = detect_available_cores()
    return max(1, int(cores * fraction))

# ------------------------------ Discovery (uses 'topologies') ------------------------------ #

def csc_path_for(ararl_dir: Path, nodes: int, topo_id: str) -> Path:
    p = ararl_dir / f"topologies/N{nodes}/simulation-nodes{nodes}-topo{topo_id}.csc"
    if p.is_file(): return p
    p_single = ararl_dir / f"simulation-nodes{nodes}.csc"
    if p_single.is_file() and topo_id in ("01", "1"): return p_single
    raise FileNotFoundError(f"CSC not found for N{nodes}, topo {topo_id}: {p} or {p_single}")

def pos_header_for(ararl_dir: Path, nodes: int, topo_id: str) -> Path:
    p = ararl_dir / f"topologies/N{nodes}/positions-simulation-nodes{nodes}-topo{topo_id}.h"
    if p.is_file(): return p
    p_single = ararl_dir / f"positions-simulation-nodes{nodes}.h"
    if p_single.is_file() and topo_id in ("01", "1"): return p_single
    raise FileNotFoundError(f"Positions header not found for N{nodes}, topo {topo_id}: {p} or {p_single}")

def makefile_for_ppm(ararl_dir: Path, ppm: int) -> Path:
    p = ararl_dir / f"Makefile-ppm{ppm}"
    if not p.is_file():
        raise FileNotFoundError(f"Makefile for ppm {ppm} not found: {p}")
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
    delay_latency: List[float] = field(default_factory=list)   # ms, per-node delay from sink summary
    nlt: Optional[float] = None                              # ms, first END_ENERGY
    prr: Optional[float] = None                              # total recv / total gen
    total_gen: int = 0
    total_fwd: int = 0
    total_recv: int = 0
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
                        m.total_fwd += val
                    ql = re.search(r'QLoss=(\d+)', line)
                    if ql:
                        val = int(ql.group(1))
                        node_data[nid]['qloss'] = val
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
                    av = re.search(r'AvgDelay=(\d+(?:\.\d+)?)(?:ms)?', line)
                    if av:
                        e = float(av.group(1))
                        node_data[nid]['delay'] = e
                        m.delay_latency.append(e)
    except Exception as e:
        print(f"[WARN] parse failed {log_path}: {e}", file=sys.stderr)
        return None

    m.node_count = len(node_data)
    if first_death_ms is not None:
        m.nlt = float(first_death_ms)
    elif node_data:
        max_end = max((n.get('end_ms', 0) for n in node_data.values()), default=0)
        if max_end > 0: m.nlt = float(max_end)
    if m.total_gen > 0:
        m.prr = m.total_recv / m.total_gen
    return m

@dataclass
class AggregatedMetrics:
    delay_mean: Optional[float]=None; delay_std: Optional[float]=None; delay_min: Optional[float]=None; delay_max: Optional[float]=None
    nlt_mean: Optional[float]=None; nlt_std: Optional[float]=None; nlt_min: Optional[float]=None; nlt_max: Optional[float]=None
    prr_mean: Optional[float]=None; prr_std: Optional[float]=None; prr_min: Optional[float]=None; prr_max: Optional[float]=None
    run_count: int = 0; valid_runs: int = 0

def aggregate_metrics(items: List[Optional[RunMetrics]]) -> AggregatedMetrics:
    agg = AggregatedMetrics()
    vals_delay: List[float]=[]; vals_nlt: List[float]=[]; vals_prr: List[float]=[]
    agg.run_count = len(items); agg.valid_runs = len([x for x in items if x is not None])
    for m in items:
        if m is None: continue
        vals_delay.extend(m.delay_latency)
        if m.nlt is not None: vals_nlt.append(m.nlt)
        if m.prr is not None: vals_prr.append(m.prr)
    if vals_delay:
        agg.delay_mean = statistics.mean(vals_delay)
        agg.delay_std  = statistics.stdev(vals_delay) if len(vals_delay)>1 else 0.0
        agg.delay_min, agg.delay_max = min(vals_delay), max(vals_delay)
    if vals_nlt:
        agg.nlt_mean = statistics.mean(vals_nlt)
        agg.nlt_std  = statistics.stdev(vals_nlt) if len(vals_nlt)>1 else 0.0
        agg.nlt_min, agg.nlt_max = min(vals_nlt), max(vals_nlt)
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
        if agg.delay_mean is not None: w.writerow(['Delay', f'{agg.delay_mean:.2f}', f'{agg.delay_std:.2f}', f'{agg.delay_min:.2f}', f'{agg.delay_max:.2f}', 'ms'])
        if agg.nlt_mean is not None: w.writerow(['NLT', f'{agg.nlt_mean:.2f}', f'{agg.nlt_std:.2f}', f'{agg.nlt_min:.2f}', f'{agg.nlt_max:.2f}', 'ms'])
        if agg.prr_mean is not None: w.writerow(['PRR', f'{agg.prr_mean:.4f}', f'{agg.prr_std:.4f}', f'{agg.prr_min:.4f}', f'{agg.prr_max:.4f}', 'ratio'])
        w.writerow([]); w.writerow(['Runs', agg.run_count, 'Valid', agg.valid_runs, '', ''])

def write_aggregated_json(agg: AggregatedMetrics, out_json: Path) -> None:
    ensure_dir(out_json.parent)
    data = {"runs":{"total":agg.run_count,"valid":agg.valid_runs},"metrics":{}}
    if agg.delay_mean is not None: data["metrics"]["Delay"]={"mean":agg.delay_mean,"std":agg.delay_std,"min":agg.delay_min,"max":agg.delay_max,"unit":"ms"}
    if agg.nlt_mean is not None: data["metrics"]["NLT"]={"mean":agg.nlt_mean,"std":agg.nlt_std,"min":agg.nlt_min,"max":agg.nlt_max,"unit":"ms"}
    if agg.prr_mean is not None: data["metrics"]["PRR"]={"mean":agg.prr_mean,"std":agg.prr_std,"min":agg.prr_min,"max":agg.prr_max,"unit":"ratio"}
    out_json.write_text(json.dumps(data, indent=2), encoding='utf-8')

def append_run_summary_row(
    base_logs: Path,
    n: int,
    ppm: int,
    topo: str,
    seed: int,
    mask: str,
    m: RunMetrics,
    existing_keys: Optional[Set[SummaryKey]] = None,
) -> None:
    row_key = _summary_key(n, ppm, topo, seed, mask)
    if existing_keys is not None and row_key in existing_keys:
        return

    csv_path = base_logs / "summary.csv"
    new = not csv_path.exists()
    with csv_path.open("a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["nodes","ppm","topo","seed","mask","prr","delay_ms","nlt_ms","gen","recv"])
        prr = m.prr if m.prr is not None else ""
        delay_ms = (statistics.mean(m.delay_latency) if m.delay_latency else "")
        nlt = m.nlt if m.nlt is not None else ""
        w.writerow([n, ppm, topo, seed, mask,
                    f"{prr:.6f}" if prr!="" else "",
                    f"{delay_ms:.2f}" if delay_ms!="" else "",
                    int(nlt) if nlt!="" else "",
                    m.total_gen, m.total_recv])
    if existing_keys is not None:
        existing_keys.add(row_key)

# ------------------------------ One run (isolated workspace) ------------------------------ #

def _clone_workspace(src: Path, dst: Path) -> None:
    if dst.exists(): shutil.rmtree(dst, ignore_errors=True)
    dst.parent.mkdir(parents=True, exist_ok=True)
    # Fast path: rsync with hard-links; fallback to copytree
    try:
        subprocess.check_call(["rsync", "-a", "--delete", "--hard-links", f"{src}/", f"{dst}/"])
    except Exception:
        shutil.copytree(src, dst)

def run_block(cfg: RunnerConfig, mask: MaskSpec, n: int, ppm: int, topo: str, seed: int) -> Tuple[bool, str]:
    # Prepare paths
    run_dir = run_dir_for(cfg.logs_dir, mask.name, n, ppm, topo, seed)
    work_dir = cfg.work_root / f"N{n}_PPM{ppm}" / f"topo{topo}" / f"seed{seed}" / mask.name

    if run_dir.exists():
        if run_dir.is_dir():
            shutil.rmtree(run_dir, ignore_errors=True)
        else:
            try:
                run_dir.unlink()
            except Exception:
                pass
    ensure_dir(run_dir)

    try:
        # Resolve inputs
        csc_src = csc_path_for(cfg.ararl_dir, n, topo)
        pos_src = pos_header_for(cfg.ararl_dir, n, topo)
        mk_src  = makefile_for_ppm(cfg.ararl_dir, ppm)
        if not mask.file.is_file():
            raise FileNotFoundError(f"Mask file not found: {mask.file}")

        # Isolated workspace
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
            "MASK_NAME": mask.name,
            "MASK_FILE": str(mask.file.resolve()),
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
            "tx_range": cfg.tx_range, "int_range": cfg.int_range,
            "mask_name": mask.name, "mask_file": str(mask.file),
            "mask_enabled": read_mask_enabled(mask.file),
            "ararl_dir": str(cfg.ararl_dir.resolve()),
            "csc_src": str(csc_src), "pos_header_src": str(pos_src),
            "ppm_makefile_src": str(mk_src), "gradle_root": str(cfg.gradle_root.resolve()),
            "work_dir": str(work_dir.resolve()), "run_dir": str(run_dir.resolve()),
        }
        write_text(run_dir / "run_meta.yaml", yaml_dump(meta))

        if cfg.dry_run:
            print(f"[DRY-RUN] {' '.join(cmd)}", flush=True)
            return True, str(run_dir)

        # Execute in the workspace (so COOJA.testlog is unique)
        exit_code = sh(
            cmd,
            cwd=work_dir,
            env=env,
            out_path=run_dir / "runner.log",
        )
        if exit_code != 0:
            print(f"[ERR] Runner exited non-zero: code={exit_code} run={run_dir}", flush=True)
            tail = tail_lines(run_dir / "runner.log", n=cfg.error_log_tail)
            if tail:
                if cfg.error_log_tail == 0:
                    print("[ERR] Full runner.log:", flush=True)
                else:
                    print(f"[ERR] Last {cfg.error_log_tail} lines of runner.log:", flush=True)
                for ln in tail:
                    print(f"  {ln}", flush=True)
            else:
                print("[ERR] runner.log not found or unreadable.", flush=True)
            return False, str(run_dir)

        # Handle logs
        log_src = work_dir / "COOJA.testlog"
        if log_src.exists():
            shutil.move(str(log_src), str(run_dir / "COOJA.testlog"))

        ok, _stats = basic_log_health(run_dir / "COOJA.testlog", expected_nodes=n)

        agent_src = work_dir / "agent.log"
        if agent_src.exists():
            shutil.copy2(agent_src, run_dir / "agent.log")

        return ok, str(run_dir)
    except Exception as e:
        print(f"[ERR] run failed ({mask.name}, N{n}, PPM{ppm}, topo{topo}, seed{seed}): {e}", flush=True)
        return False, str(run_dir)
    finally:
        if not cfg.keep_work:
            shutil.rmtree(work_dir, ignore_errors=True)

# ------------------------------ CLI/Main ------------------------------ #

def _safe_mask_name(s: str) -> str:
    name = re.sub(r"[^A-Za-z0-9_-]+", "_", s).strip("_")
    return name or "mask"

def _resolve_masks(mask_input: Path, single_mask_name: Optional[str]) -> List[MaskSpec]:
    if not mask_input.exists():
        die(f"Mask path not found: {mask_input}")

    if mask_input.is_file():
        name = _safe_mask_name(single_mask_name) if single_mask_name else auto_mask_name_from_file(mask_input)
        return [MaskSpec(name=name, file=mask_input.resolve())]

    if not mask_input.is_dir():
        die(f"Mask path must be a file or directory: {mask_input}")

    if single_mask_name:
        die("--mask-name can only be used when --mask-file points to a single file.")

    files = sorted([p for p in mask_input.glob("*.yaml") if p.is_file()])
    files.extend(sorted([p for p in mask_input.glob("*.yml") if p.is_file()]))
    if not files:
        die(f"No YAML masks found in directory: {mask_input}")

    masks: List[MaskSpec] = []
    used_names: Dict[str, int] = {}
    for p in files:
        base = _safe_mask_name(p.stem)
        count = used_names.get(base, 0) + 1
        used_names[base] = count
        name = base if count == 1 else f"{base}_{count}"
        masks.append(MaskSpec(name=name, file=p.resolve()))
    return masks

def parse_args() -> RunnerConfig:
    ap = argparse.ArgumentParser(
        description="Parallel Cooja runner with isolated workspaces + aggregation (paper metrics).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    ap.add_argument("--ararl-dir", type=Path, required=False, help="experiments/ararl dir (contains topologies/, *.c, Makefile-ppm*)")
    ap.add_argument("--logs-dir", type=Path, default=Path("testbed/logs"))
    ap.add_argument("--gradle-root", type=Path, default=Path("contiki-ng/tools/cooja"))
    ap.add_argument("--nodes", type=int, nargs="+", default=[60,80,100])
    ap.add_argument("--ppm", type=int, nargs="+", default=[80,100,120])
    ap.add_argument("--topologies", "--topology-count", dest="topologies", type=int, default=10,
                    help="Number of topo IDs (01..NN) if --topology-ids not given")
    ap.add_argument("--topology-ids", type=str, nargs="*", help="Explicit topo IDs like 01 02 03")
    ap.add_argument("--mask-file", type=Path, required=False, help="Mask YAML file OR directory containing multiple mask YAML files")
    ap.add_argument("--mask-name", type=str, default=None, help="Name tag for single mask mode only")
    ap.add_argument("--duration-sf", type=int, default=180)
    ap.add_argument("--warmup-sf", type=int, default=12)
    ap.add_argument("--sim-seed", type=int, default=67890)
    ap.add_argument("--agent-seed", type=int, default=12345)
    seed_group = ap.add_mutually_exclusive_group()
    seed_group.add_argument("--traffic-seeds", type=int, nargs="+", default=None,
                            help="Explicit traffic seed IDs, e.g. --traffic-seeds 1 2 3")
    seed_group.add_argument("--seed-count", "--seeds", dest="seed_count", type=int, default=None,
                            help="Number of traffic seeds to generate as 1..N")
    ap.add_argument("--tx-range", type=float, default=None)
    ap.add_argument("--int-range", type=float, default=None)
    ap.add_argument("--gradle-user-home", type=Path, default=None,
                    help="Gradle user home/cache path. Default: inherit environment (recommended with Docker volume).")
    ap.add_argument("--jobs", type=int, default=0, help="Max concurrent runs. 0 = auto (use 80%% of allowed cores)")
    ap.add_argument("--heartbeat-secs", type=int, default=60,
                    help="Heartbeat interval in seconds for stalled/no-completion status. 0 disables heartbeat.")
    ap.add_argument("--work-root", type=Path, default=Path("testbed/_work"), help="Where per-run temp workspaces go")
    ap.add_argument("--keep-work", action="store_true", help="Keep workspaces (debug)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--resume", action="store_true",
                    help="Resume from logs-dir/run_checkpoint.json and run only unfinished/invalid tasks.")
    ap.add_argument("--error-log-tail", type=int, default=50,
                    help="Lines from end of runner.log to print on non-zero exit. 0 = print full file.")

    a = ap.parse_args()

    logs_dir_resolved = a.logs_dir.resolve()
    ck_path = checkpoint_path_for(logs_dir_resolved)
    if a.resume:
        ck_status, ck, ck_reason = inspect_checkpoint(ck_path)
        if ck_status == "ok" and ck is not None:
            try:
                cfg = config_from_dict(ck["config"])
            except BaseException as e:
                if isinstance(e, KeyboardInterrupt):
                    raise
                detail = str(e) if str(e) else e.__class__.__name__
                die(f"--resume requested but checkpoint at {ck_path} has invalid config: {detail}")
            cfg.resume = True
            cfg.logs_dir = logs_dir_resolved
            print(f"[INFO] Loaded resume config from checkpoint: {ck_path}")
            return cfg
        if ck_status == "invalid":
            die(f"--resume requested but checkpoint at {ck_path} is invalid: {ck_reason}")
        if a.ararl_dir is None or a.mask_file is None:
            die(
                f"--resume requested but checkpoint not found at {ck_path}. "
                "Provide full run config to start a new checkpointed run."
            )
        print(f"[INFO] No checkpoint found at {ck_path}; starting a fresh run and creating checkpoint.")

    if a.ararl_dir is None:
        die("--ararl-dir is required unless --resume can load an existing checkpoint.")
    if a.mask_file is None:
        die("--mask-file is required unless --resume can load an existing checkpoint.")

    if (not a.topology_ids) and a.topologies < 1:
        die("--topologies must be >= 1")
    if a.seed_count is not None and a.seed_count < 1:
        die("--seed-count must be >= 1")

    nodes = dedupe_preserve_order([int(x) for x in a.nodes])
    ppms = dedupe_preserve_order([int(x) for x in a.ppm])
    traffic_seeds = list(range(1, a.seed_count + 1)) if a.seed_count is not None else (a.traffic_seeds or [1])
    traffic_seeds = dedupe_preserve_order([int(x) for x in traffic_seeds])
    if any(s < 1 for s in traffic_seeds):
        die("--traffic-seeds values must be >= 1")

    # topology id list
    if a.topology_ids:
        topo_ids = dedupe_preserve_order([str(x) for x in a.topology_ids])
    else:
        width = max(2, len(str(a.topologies)))   # always at least 2 digits
        topo_ids = [str(i).zfill(width) for i in range(1, a.topologies + 1)]

    # Resolve masks and jobs
    masks = _resolve_masks(a.mask_file.resolve(), a.mask_name)
    jobs = auto_jobs_from_cores(0.8) if a.jobs == 0 else max(1, a.jobs)

    return RunnerConfig(
        ararl_dir=a.ararl_dir.resolve(),
        logs_dir=logs_dir_resolved,
        gradle_root=a.gradle_root.resolve(),
        nodes=nodes,
        ppms=ppms,
        topology_ids=topo_ids,
        masks=masks,
        duration_sf=a.duration_sf,
        warmup_sf=a.warmup_sf,
        sim_seed=a.sim_seed,
        agent_seed=a.agent_seed,
        traffic_seeds=traffic_seeds,
        tx_range=a.tx_range,
        int_range=a.int_range,
        gradle_user_home=(a.gradle_user_home.resolve() if a.gradle_user_home else None),
        jobs=jobs,
        work_root=a.work_root.resolve(),
        keep_work=a.keep_work,
        dry_run=a.dry_run,
        resume=a.resume,
        error_log_tail=max(0, a.error_log_tail),
        heartbeat_secs=max(0, a.heartbeat_secs),
    )

def main() -> None:
    started_at = time.time()
    cfg = parse_args()

    # Pre-clean workspace so old aborted runs can't leak anything
    if not cfg.keep_work and cfg.work_root.exists():
        shutil.rmtree(cfg.work_root, ignore_errors=True)

    ensure_dir(cfg.logs_dir)
    ensure_dir(cfg.work_root)

    # Prepare all tasks
    tasks = build_tasks(cfg)
    total_tasks = len(tasks)
    mask_by_name = {m.name: m for m in cfg.masks}

    # Checkpoint setup
    ck_path = checkpoint_path_for(cfg.logs_dir)
    loaded_existing_checkpoint = False
    checkpoint: Optional[Dict[str, Any]] = None
    if cfg.resume:
        ck_status, ck, ck_reason = inspect_checkpoint(ck_path)
        if ck_status == "ok":
            checkpoint = ck
        elif ck_status == "invalid":
            die(f"Checkpoint at {ck_path} is invalid: {ck_reason}")
    if checkpoint is None:
        checkpoint = init_checkpoint(cfg, tasks)
        save_checkpoint(ck_path, checkpoint)
        msg = "Created new checkpoint" if cfg.resume else "Initialized checkpoint"
        print(f"[INFO] {msg}: {ck_path}", flush=True)
    else:
        loaded_existing_checkpoint = True
        cfg_dump = config_to_dict(cfg)
        checkpoint_changed = (checkpoint.get("config") != cfg_dump)
        if checkpoint.get("version") != CHECKPOINT_VERSION:
            checkpoint["version"] = CHECKPOINT_VERSION
            checkpoint_changed = True
        checkpoint["config"] = cfg_dump
        task_state = checkpoint.setdefault("task_state", {})
        for task in tasks:
            tid = task_id(*task)
            if not isinstance(task_state.get(tid), dict):
                set_task_state(checkpoint, task, "pending", ok=False)
                checkpoint_changed = True
        if checkpoint_changed:
            save_checkpoint(ck_path, checkpoint)

    # Resume support (checkpoint + log/meta validation)
    results: Dict[TaskKey, Tuple[bool, str]] = {}
    resumed_tasks = 0
    tasks_to_run: List[TaskKey] = list(tasks)
    if cfg.resume and loaded_existing_checkpoint:
        pending: List[TaskKey] = []
        checkpoint_changed = False
        task_state = checkpoint.get("task_state", {})
        for task in tasks:
            (mask_name, n, ppm, topo, seed) = task
            tid = task_id(mask_name, n, ppm, topo, seed)
            entry = task_state.get(tid, {})
            if not isinstance(entry, dict):
                entry = {}
            run_dir_str = str(entry.get("run_dir") or run_dir_for(cfg.logs_dir, mask_name, n, ppm, topo, seed))
            run_dir = Path(run_dir_str)
            log_path = run_dir / "COOJA.testlog"
            meta = load_run_meta(run_dir / "run_meta.yaml")
            meta_ok = meta is not None and run_meta_matches(cfg, mask_by_name[mask_name], n, ppm, topo, seed, meta)
            health_ok, _stats = basic_log_health(log_path, expected_nodes=n)
            status = str(entry.get("status", "pending"))
            ok_flag = (entry.get("ok") is True)

            if status == "done" and ok_flag and health_ok and meta_ok:
                results[task] = (True, str(run_dir))
                resumed_tasks += 1
            else:
                pending.append(task)
                set_task_state(checkpoint, task, "pending", ok=False, run_dir=str(run_dir))
                checkpoint_changed = True
        tasks_to_run = pending
        if checkpoint_changed:
            save_checkpoint(ck_path, checkpoint)
        print(
            f"RESUME STATUS: recovered={resumed_tasks}, to_run={len(tasks_to_run)}, total={total_tasks}",
            flush=True,
        )

    # Mark scheduled tasks as running before execution so crash recovery can retry them.
    if tasks_to_run:
        for task in tasks_to_run:
            (mask_name, n, ppm, topo, seed) = task
            set_task_state(
                checkpoint,
                task,
                "running",
                ok=False,
                run_dir=str(run_dir_for(cfg.logs_dir, mask_name, n, ppm, topo, seed)),
            )
        save_checkpoint(ck_path, checkpoint)

    # Run in parallel
    max_workers = min(cfg.jobs, len(tasks_to_run)) if tasks_to_run else 1
    initial_in_progress = min(max_workers, len(tasks_to_run)) if tasks_to_run else 0
    initial_completed = resumed_tasks
    initial_remaining = max(0, total_tasks - initial_completed)
    print(f"MAX PARALLEL WORKERS: {max_workers}", flush=True)
    print(
        f"TASK STATUS: total={total_tasks}, in_progress={initial_in_progress}, remaining={initial_remaining}, completed={initial_completed}",
        flush=True,
    )

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = {
            ex.submit(run_block, cfg, mask_by_name[mask_name], n, ppm, topo, seed): (mask_name, n, ppm, topo, seed)
            for (mask_name, n, ppm, topo, seed) in tasks_to_run
        }
        completed_tasks = resumed_tasks
        last_completion_ts = time.time()
        pending = set(futs.keys())
        heartbeat_timeout = None if cfg.heartbeat_secs == 0 else cfg.heartbeat_secs

        while pending:
            done, pending = wait(pending, timeout=heartbeat_timeout, return_when=FIRST_COMPLETED)

            if not done:
                now = time.time()
                remaining_tasks = max(0, total_tasks - completed_tasks)
                in_progress_tasks = min(max_workers, remaining_tasks) if remaining_tasks > 0 else 0
                print(
                    f"HEARTBEAT: elapsed={fmt_hms(now - started_at)} no_completion_for={fmt_hms(now - last_completion_ts)} "
                    f"total={total_tasks}, in_progress={in_progress_tasks}, remaining={remaining_tasks}, completed={completed_tasks}",
                    flush=True,
                )
                continue

            for fut in done:
                key = futs[fut]
                (mask_name, n, ppm, topo, seed) = key
                try:
                    ok, rdir = fut.result()
                except BaseException as e:
                    if isinstance(e, KeyboardInterrupt):
                        raise
                    ok, rdir = False, ""
                    print(f"[ERR] run failed ({mask_name}, N{n}, PPM{ppm}, topo{topo}, seed{seed}): {e}", flush=True)
                if not rdir:
                    rdir = str(run_dir_for(cfg.logs_dir, mask_name, n, ppm, topo, seed))
                results[key] = (ok, rdir)
                set_task_state(
                    checkpoint,
                    key,
                    "done" if ok else "failed",
                    ok=ok,
                    run_dir=rdir,
                )
                save_checkpoint(ck_path, checkpoint)
                completed_tasks += 1
                last_completion_ts = time.time()
                remaining_tasks = max(0, total_tasks - completed_tasks)
                in_progress_tasks = min(max_workers, remaining_tasks) if remaining_tasks > 0 else 0
                print(
                    f"TASK STATUS: total={total_tasks}, in_progress={in_progress_tasks}, remaining={remaining_tasks}, completed={completed_tasks}",
                    flush=True,
                )

    mask_metrics: Dict[Tuple[str, int, int], List[Optional[RunMetrics]]] = {}

    # Strict latest-only outputs: rewrite summary and clear stale aggregate files for this config.
    summary_csv = cfg.logs_dir / "summary.csv"
    if summary_csv.exists():
        try:
            summary_csv.unlink()
        except Exception:
            pass
    for mask in cfg.masks:
        for n in cfg.nodes:
            for ppm in cfg.ppms:
                base = cfg.logs_dir / f"N{n}_PPM{ppm}" / mask.name
                for fname in ("aggregated_results.csv", "aggregated_results.json"):
                    p = base / fname
                    if p.exists():
                        try:
                            p.unlink()
                        except Exception:
                            pass

    # Parse + aggregate per (nodes, ppm) and append per-run summary
    for (mask_name, n, ppm, topo, seed) in sorted(results.keys()):
        ok, rdir = results[(mask_name, n, ppm, topo, seed)]
        bucket = mask_metrics.setdefault((mask_name, n, ppm), [])
        if not rdir:
            bucket.append(None)
            continue

        log_path = Path(rdir) / "COOJA.testlog"
        m = parse_log(log_path)
        bucket.append(m)

        if not m:
            continue

        # even if ok==False, still include it in aggregation
        append_run_summary_row(cfg.logs_dir, n, ppm, topo, seed, mask_name, m, existing_keys=None)

    for (mask_name, n, ppm), lst in sorted(mask_metrics.items()):
        agg = aggregate_metrics(lst)
        base = cfg.logs_dir / f"N{n}_PPM{ppm}" / mask_name
        write_aggregated_csv(agg, base / "aggregated_results.csv")
        write_aggregated_json(agg, base / "aggregated_results.json")

    total = total_tasks; ok_count = sum(1 for v in results.values() if v[0])
    failed_count = max(0, total - ok_count)
    completed = len(results)
    remaining = max(0, total_tasks - completed)
    in_progress = 0
    elapsed_s = int(time.time() - started_at)
    hh, rem = divmod(elapsed_s, 3600)
    mm, ss = divmod(rem, 60)
    print("\n" + "="*78)
    print(f"TASK STATUS: total={total_tasks}, in_progress={in_progress}, remaining={remaining}, completed={completed}")
    if failed_count == 0:
        print(f"DONE. Runs attempted: {len(tasks_to_run)}, resumed existing: {resumed_tasks}, passed basic health: {ok_count}/{total}.")
    else:
        print(
            f"FAILED. Runs attempted: {len(tasks_to_run)}, resumed existing: {resumed_tasks}, "
            f"passed basic health: {ok_count}/{total}, failed: {failed_count}."
        )
    print(f"Elapsed wall time: {hh:02d}:{mm:02d}:{ss:02d} ({elapsed_s}s)")
    print("="*78)

    # Cleanup global work root unless kept for debug
    if not cfg.keep_work:
        try: shutil.rmtree(cfg.work_root, ignore_errors=True)
        except Exception: pass

    if failed_count > 0:
        sys.exit(1)

if __name__ == "__main__":
    main()
