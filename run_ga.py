#!/usr/bin/env python3
"""
Batch-parallel GA search for feature submasks on top of run.py.

Key behavior:
- The input --mask-file defines the feature search space:
  only features set to true are searchable by GA.
- Each chromosome is a submask over that searchable set.
- Each generation is evaluated in one batched run.py call, so run.py can exploit
  maximum parallelism globally across (mask, nodes, ppm, topology, seed).

Example:
  python3 /workspace/run_ga.py \
    --mask-file /workspace/mask.yaml \
    --ararl-dir /workspace/experiments/ararl \
    --gradle-root /workspace/contiki-ng/tools/cooja \
    --work-root /workspace/_work \
    --ga-out /workspace/ga_out \
    --nodes 60 80 100 --ppm 80 100 120 --topologies 10 --traffic-seeds 1 \
    --population 16 --generations 12 --elite 2 --cx-rate 0.8 --mut-rate 0.08 \
    --jobs 24
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
import re
import shutil
import statistics
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

try:
    import yaml  # optional, for robust mask parsing
except Exception:
    yaml = None


@dataclass
class SearchSpace:
    mask_source: Path
    feature_order: List[str]
    search_features: List[str]
    all_flag: bool


@dataclass
class GAConfig:
    # Inputs / wiring
    runner_script: Path
    mask_file: Path
    ararl_dir: Path
    gradle_root: Path
    work_root: Path
    ga_out: Path

    # Matrix (forwarded to run.py)
    nodes: List[int]
    ppms: List[int]
    topologies: int
    topology_ids: List[str]
    traffic_seeds: List[int]
    duration_sf: int
    warmup_sf: int
    sim_seed: int
    agent_seed: int
    tx_range: Optional[float]
    int_range: Optional[float]
    jobs: int
    gradle_user_home: Optional[Path]
    heartbeat_secs: int
    error_log_tail: int
    keep_work: bool
    dry_run: bool
    resume: bool

    # GA controls
    population: int
    generations: int
    elite: int
    cx_rate: float
    mut_rate: float
    random_seed: int
    allow_empty_mask: bool

    # Fitness
    w_nlt: float
    w_prr: float
    w_dly: float
    delay_scale_ms: float
    prr_min: float
    lambda_stability: float
    beta_prr_penalty: float
    gamma_missing_penalty: float

    # Derived from mask file
    search_space: SearchSpace


def die(msg: str, code: int = 2) -> None:
    print(f"[FATAL] {msg}", file=sys.stderr)
    sys.exit(code)


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def dedupe_preserve_order(values: List[Any]) -> List[Any]:
    seen = set()
    out: List[Any] = []
    for v in values:
        if v in seen:
            continue
        seen.add(v)
        out.append(v)
    return out


def recreate_dir(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True, exist_ok=True)

def write_json_atomic(path: Path, payload: Dict[str, Any]) -> None:
    ensure_dir(path.parent)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(path)


def run_cmd(cmd: Sequence[str], cwd: Optional[Path] = None) -> int:
    print("[RUN]", " ".join(str(x) for x in cmd), f"(cwd={cwd})")
    try:
        proc = subprocess.Popen(list(cmd), cwd=str(cwd) if cwd else None)
        return proc.wait()
    except FileNotFoundError:
        return 127


def _parse_features_block(mask_path: Path) -> SearchSpace:
    text = mask_path.read_text(encoding="utf-8")
    if yaml is not None:
        try:
            raw = yaml.safe_load(text)
        except Exception as e:
            die(f"Mask YAML parse failed for {mask_path}: {e}")

        if not isinstance(raw, dict):
            die(f"Mask root must be a mapping: {mask_path}")
        feats_raw = raw.get("features")
        if not isinstance(feats_raw, dict):
            die(f"Mask has no valid 'features:' mapping: {mask_path}")

        seen: Dict[str, bool] = {}
        order: List[str] = []
        for k_raw, v_raw in feats_raw.items():
            key = str(k_raw).strip()
            if not key:
                continue

            val: Optional[bool]
            if isinstance(v_raw, bool):
                val = v_raw
            elif isinstance(v_raw, (int, float)) and not isinstance(v_raw, bool):
                val = bool(v_raw)
            elif isinstance(v_raw, str):
                vv = v_raw.strip().lower()
                if vv in {"true", "yes", "on", "1"}:
                    val = True
                elif vv in {"false", "no", "off", "0"}:
                    val = False
                else:
                    val = None
            else:
                val = None

            if val is None:
                die(f"Feature '{key}' in {mask_path} must be true/false.")

            if key not in seen:
                order.append(key)
            seen[key] = val

        if not seen:
            die(f"Mask features block is empty: {mask_path}")

        all_flag = bool(seen.get("all", False))
        feature_order = [k for k in order if k != "all"]
        if not feature_order:
            die(f"No feature keys found under 'features:' in {mask_path}")

        search_features = [f for f in feature_order if seen.get(f, False)]
        return SearchSpace(
            mask_source=mask_path,
            feature_order=feature_order,
            search_features=search_features,
            all_flag=all_flag,
        )

    # Fallback parser when PyYAML is unavailable.
    lines = text.splitlines()

    in_features = False
    features_indent = 0
    seen: Dict[str, bool] = {}
    order: List[str] = []

    for raw in lines:
        clean = raw.split("#", 1)[0].rstrip()
        if not clean.strip():
            continue

        indent = len(clean) - len(clean.lstrip(" "))
        stripped = clean.strip()

        if not in_features:
            if stripped == "features:":
                in_features = True
                features_indent = indent
            continue

        if indent <= features_indent:
            break

        m = re.match(r"^([A-Za-z0-9_-]+)\s*:\s*(true|false)\s*$", stripped, flags=re.IGNORECASE)
        if not m:
            continue

        key = m.group(1)
        val = m.group(2).lower() == "true"
        if key not in seen:
            order.append(key)
        seen[key] = val

    if not in_features:
        die(f"Mask has no 'features:' block: {mask_path}")
    if not seen:
        die(f"Mask features block is empty/unparseable: {mask_path}")

    all_flag = bool(seen.get("all", False))
    feature_order = [k for k in order if k != "all"]
    if not feature_order:
        die(f"No feature keys found under 'features:' in {mask_path}")

    search_features = [f for f in feature_order if seen.get(f, False)]
    return SearchSpace(
        mask_source=mask_path,
        feature_order=feature_order,
        search_features=search_features,
        all_flag=all_flag,
    )


def parse_args() -> GAConfig:
    ap = argparse.ArgumentParser(
        description="Batch-parallel GA search over submasks (calls run.py once per generation).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    default_runner = Path(__file__).resolve().with_name("run.py")
    ap.add_argument("--runner-script", type=Path, default=default_runner, help="Path to run.py")
    ap.add_argument("--mask-file", type=Path, required=True, help="Input mask that defines search space (true features are searchable)")
    ap.add_argument("--ararl-dir", type=Path, required=True)
    ap.add_argument("--gradle-root", type=Path, required=True)
    ap.add_argument("--work-root", type=Path, default=Path("testbed/_work"))
    ap.add_argument("--ga-out", type=Path, default=Path("testbed/ga_out"))

    ap.add_argument("--nodes", type=int, nargs="+", default=[60, 80, 100])
    ap.add_argument("--ppm", type=int, nargs="+", default=[80, 100, 120])
    ap.add_argument("--topologies", "--topology-count", dest="topologies", type=int, default=10,
                    help="Used when --topology-ids is not provided")
    ap.add_argument("--topology-ids", type=str, nargs="*", default=[])
    seed_group = ap.add_mutually_exclusive_group()
    seed_group.add_argument("--traffic-seeds", type=int, nargs="+", default=None,
                            help="Explicit traffic seed IDs, e.g. --traffic-seeds 1 2 3")
    seed_group.add_argument("--seed-count", "--seeds", dest="seed_count", type=int, default=None,
                            help="Number of traffic seeds to generate as 1..N")
    ap.add_argument("--duration-sf", type=int, default=180)
    ap.add_argument("--warmup-sf", type=int, default=12)
    ap.add_argument("--sim-seed", type=int, default=67890)
    ap.add_argument("--agent-seed", type=int, default=12345)
    ap.add_argument("--tx-range", type=float, default=None)
    ap.add_argument("--int-range", type=float, default=None)
    ap.add_argument("--jobs", type=int, default=0, help="Forwarded to run.py --jobs")
    ap.add_argument("--gradle-user-home", type=Path, default=None)
    ap.add_argument("--heartbeat-secs", type=int, default=60)
    ap.add_argument("--error-log-tail", type=int, default=50)
    ap.add_argument("--keep-work", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--resume", action="store_true",
                    help="Resume GA from ga-out checkpoints (state/cache), and continue up to --generations.")

    ap.add_argument("--population", type=int, default=16)
    ap.add_argument("--generations", type=int, default=12)
    ap.add_argument("--elite", type=int, default=2)
    ap.add_argument("--cx-rate", type=float, default=0.8)
    ap.add_argument("--mut-rate", type=float, default=0.08)
    ap.add_argument("--random-seed", type=int, default=1337)
    ap.add_argument("--allow-empty-mask", action="store_true", help="Allow evaluating empty submask")

    # Default weights per your request: NLT 50%, PRR 25%, Delay 25%.
    ap.add_argument("--w-nlt", type=float, default=0.50)
    ap.add_argument("--w-prr", type=float, default=0.25)
    ap.add_argument("--w-dly", type=float, default=0.25)
    ap.add_argument("--delay-scale-ms", type=float, default=1000.0)
    ap.add_argument("--prr-min", type=float, default=0.85)
    ap.add_argument("--lambda-stability", type=float, default=0.10)
    ap.add_argument("--beta-prr-penalty", type=float, default=2.0)
    ap.add_argument("--gamma-missing-penalty", type=float, default=1.0)

    a = ap.parse_args()

    runner_script = a.runner_script.resolve()
    if not runner_script.is_file():
        die(f"runner script not found: {runner_script}")

    mask_file = a.mask_file.resolve()
    if not mask_file.is_file():
        die(f"mask file not found: {mask_file}")

    search_space = _parse_features_block(mask_file)
    if search_space.all_flag:
        die("features.all must be false in --mask-file for GA submask search.")
    if not search_space.search_features and not a.allow_empty_mask:
        die("No searchable features are true in --mask-file. Set at least one feature true, or use --allow-empty-mask.")

    if a.population < 1:
        die("--population must be >= 1")
    if a.generations < 1:
        die("--generations must be >= 1")
    if a.elite < 0:
        die("--elite must be >= 0")
    if not (0.0 <= a.cx_rate <= 1.0):
        die("--cx-rate must be in [0,1]")
    if not (0.0 <= a.mut_rate <= 1.0):
        die("--mut-rate must be in [0,1]")
    if a.delay_scale_ms <= 0:
        die("--delay-scale-ms must be > 0")
    if a.duration_sf <= 0:
        die("--duration-sf must be > 0")
    if (not a.topology_ids) and a.topologies < 1:
        die("--topologies must be >= 1")
    if a.seed_count is not None and a.seed_count < 1:
        die("--seed-count must be >= 1")

    nodes = dedupe_preserve_order([int(x) for x in a.nodes])
    ppms = dedupe_preserve_order([int(x) for x in a.ppm])
    topology_ids = dedupe_preserve_order([str(x) for x in a.topology_ids])
    traffic_seeds = list(range(1, a.seed_count + 1)) if a.seed_count is not None else (a.traffic_seeds or [1])
    traffic_seeds = dedupe_preserve_order([int(x) for x in traffic_seeds])
    if any(s < 1 for s in traffic_seeds):
        die("--traffic-seeds values must be >= 1")

    return GAConfig(
        runner_script=runner_script,
        mask_file=mask_file,
        ararl_dir=a.ararl_dir.resolve(),
        gradle_root=a.gradle_root.resolve(),
        work_root=a.work_root.resolve(),
        ga_out=a.ga_out.resolve(),
        nodes=nodes,
        ppms=ppms,
        topologies=a.topologies,
        topology_ids=topology_ids,
        traffic_seeds=traffic_seeds,
        duration_sf=a.duration_sf,
        warmup_sf=a.warmup_sf,
        sim_seed=a.sim_seed,
        agent_seed=a.agent_seed,
        tx_range=a.tx_range,
        int_range=a.int_range,
        jobs=a.jobs,
        gradle_user_home=(a.gradle_user_home.resolve() if a.gradle_user_home else None),
        heartbeat_secs=max(0, a.heartbeat_secs),
        error_log_tail=max(0, a.error_log_tail),
        keep_work=a.keep_work,
        dry_run=a.dry_run,
        resume=a.resume,
        population=a.population,
        generations=a.generations,
        elite=a.elite,
        cx_rate=a.cx_rate,
        mut_rate=a.mut_rate,
        random_seed=a.random_seed,
        allow_empty_mask=a.allow_empty_mask,
        w_nlt=a.w_nlt,
        w_prr=a.w_prr,
        w_dly=a.w_dly,
        delay_scale_ms=a.delay_scale_ms,
        prr_min=a.prr_min,
        lambda_stability=a.lambda_stability,
        beta_prr_penalty=a.beta_prr_penalty,
        gamma_missing_penalty=a.gamma_missing_penalty,
        search_space=search_space,
    )


def _sanitize_name(s: str) -> str:
    out = re.sub(r"[^A-Za-z0-9_-]+", "_", s).strip("_")
    return out or "mask"


def active_features(bits: Sequence[int], search_features: Sequence[str]) -> List[str]:
    return [f for b, f in zip(bits, search_features) if b == 1]


def mask_label(bits: Sequence[int], search_features: Sequence[str]) -> str:
    feats = active_features(bits, search_features)
    if not feats:
        return "none"
    return _sanitize_name("-".join(feats))


def mask_id(bits: Sequence[int], search_features: Sequence[str]) -> str:
    # Include bit signature for uniqueness even if labels collide after sanitization.
    bits_sig = "".join("1" if b else "0" for b in bits)
    return f"{mask_label(bits, search_features)}__{bits_sig}"


def valid_bits(bits: Sequence[int], allow_empty_mask: bool) -> bool:
    return allow_empty_mask or any(b == 1 for b in bits)


def random_valid_bits(num_features: int, allow_empty_mask: bool) -> List[int]:
    if num_features == 0:
        return []
    for _ in range(1024):
        bits = [1 if random.random() < 0.5 else 0 for _ in range(num_features)]
        if valid_bits(bits, allow_empty_mask):
            return bits
    bits = [0] * num_features
    if not allow_empty_mask:
        bits[random.randrange(num_features)] = 1
    return bits


def mutate(bits: Sequence[int], rate: float, allow_empty_mask: bool) -> List[int]:
    out = [(1 - b) if random.random() < rate else int(b) for b in bits]
    if not valid_bits(out, allow_empty_mask) and out:
        out[random.randrange(len(out))] = 1
    return out


def crossover(a: Sequence[int], b: Sequence[int]) -> Tuple[List[int], List[int]]:
    if len(a) < 2:
        return list(a), list(b)
    cut = random.randint(1, len(a) - 1)
    return list(a[:cut] + b[cut:]), list(b[:cut] + a[cut:])


def tournament_select(pool: Sequence[Tuple[List[int], float]], k: int = 3) -> List[int]:
    picks = random.sample(list(pool), min(k, len(pool)))
    picks.sort(key=lambda x: x[1], reverse=True)
    return list(picks[0][0])


def build_submask_yaml(
    bits: Sequence[int],
    feature_order: Sequence[str],
    search_features: Sequence[str],
    source_mask_name: str,
) -> str:
    active = set(active_features(bits, search_features))
    mid = mask_label(bits, search_features)

    lines: List[str] = []
    lines.append("run:")
    lines.append(f"  id: {mid}")
    lines.append(f"  notes: GA-generated submask from {source_mask_name}")
    lines.append("")
    lines.append("features:")
    lines.append("  all: false")
    for feat in feature_order:
        lines.append(f"  {feat}: {'true' if feat in active else 'false'}")
    return "\n".join(lines) + "\n"


def _metric_mean(data: Dict, metric_name: str) -> Optional[float]:
    try:
        return float(data["metrics"][metric_name]["mean"])
    except Exception:
        return None


def _runs_count(data: Dict, field: str) -> Optional[int]:
    try:
        return int(data["runs"][field])
    except Exception:
        return None


def compute_fitness(
    cfg: GAConfig,
    scenarios: List[Dict],
    expected_scenarios: int,
    expected_runs_per_scenario: int,
) -> Tuple[float, Dict]:
    if not scenarios:
        return -1e9, {
            "reason": "no_scenarios",
            "expected_scenarios": expected_scenarios,
            "found_scenarios": 0,
        }

    duration_ms = max(1.0, cfg.duration_sf * 1000.0)
    delay_scale = max(1.0, cfg.delay_scale_ms)

    per_s_scores: List[float] = []
    prr_shortfalls: List[float] = []
    prr_vals: List[float] = []
    nlt_vals: List[float] = []
    delay_vals: List[float] = []
    runs_total_vals: List[int] = []
    runs_valid_vals: List[int] = []
    scenario_coverage_gaps: List[float] = []

    for sc in scenarios:
        prr_raw = sc.get("prr")
        nlt_raw = sc.get("nlt")
        delay_raw = sc.get("delay")
        runs_total_raw = sc.get("runs_total")
        runs_valid_raw = sc.get("runs_valid")

        prr_norm = min(1.0, max(0.0, float(prr_raw) if prr_raw is not None else 0.0))
        nlt_norm = min(1.0, max(0.0, (float(nlt_raw) / duration_ms) if nlt_raw is not None else 0.0))
        if delay_raw is None:
            delay_score = 0.0
        else:
            delay_score = math.exp(-max(0.0, float(delay_raw)) / delay_scale)

        score = (cfg.w_prr * prr_norm) + (cfg.w_nlt * nlt_norm) + (cfg.w_dly * delay_score)
        per_s_scores.append(score)
        prr_shortfalls.append(max(0.0, cfg.prr_min - prr_norm))

        if prr_raw is not None:
            prr_vals.append(float(prr_raw))
        if nlt_raw is not None:
            nlt_vals.append(float(nlt_raw))
        if delay_raw is not None:
            delay_vals.append(float(delay_raw))
        if runs_total_raw is not None:
            runs_total_vals.append(int(runs_total_raw))
        if runs_valid_raw is not None:
            runs_valid_vals.append(int(runs_valid_raw))

        if expected_runs_per_scenario > 0:
            observed = runs_valid_raw if runs_valid_raw is not None else runs_total_raw
            observed_norm = max(0.0, float(observed) if observed is not None else 0.0)
            coverage = min(1.0, observed_norm / float(expected_runs_per_scenario))
        else:
            coverage = 1.0
        scenario_coverage_gaps.append(1.0 - coverage)

    if not per_s_scores:
        return -1e9, {
            "reason": "no_valid_scores",
            "expected_scenarios": expected_scenarios,
            "found_scenarios": len(scenarios),
        }

    mu = sum(per_s_scores) / len(per_s_scores)
    sigma = statistics.pstdev(per_s_scores) if len(per_s_scores) > 1 else 0.0
    prr_pen = cfg.beta_prr_penalty * (sum(prr_shortfalls) / len(prr_shortfalls))

    if expected_scenarios > 0:
        structural_missing = max(0.0, expected_scenarios - len(scenarios))
        partial_missing = sum(scenario_coverage_gaps)
        missing_fraction = (structural_missing + partial_missing) / expected_scenarios
        missing_fraction = min(1.0, max(0.0, missing_fraction))
    else:
        missing_fraction = 0.0
    miss_pen = cfg.gamma_missing_penalty * missing_fraction

    fitness = mu - (cfg.lambda_stability * sigma) - prr_pen - miss_pen

    detail = {
        "mu": mu,
        "sigma": sigma,
        "prr_penalty": prr_pen,
        "missing_penalty": miss_pen,
        "missing_fraction": missing_fraction,
        "score_mean": mu,
        "prr_mean": (sum(prr_vals) / len(prr_vals)) if prr_vals else None,
        "nlt_mean": (sum(nlt_vals) / len(nlt_vals)) if nlt_vals else None,
        "delay_mean": (sum(delay_vals) / len(delay_vals)) if delay_vals else None,
        "runs_total_mean": (sum(runs_total_vals) / len(runs_total_vals)) if runs_total_vals else None,
        "runs_valid_mean": (sum(runs_valid_vals) / len(runs_valid_vals)) if runs_valid_vals else None,
        "expected_runs_per_scenario": expected_runs_per_scenario,
        "scenario_coverage_mean": (1.0 - (sum(scenario_coverage_gaps) / len(scenario_coverage_gaps)))
        if scenario_coverage_gaps
        else None,
        "expected_scenarios": expected_scenarios,
        "found_scenarios": len(scenarios),
    }
    return fitness, detail


def runner_cmd_for_generation(
    cfg: GAConfig,
    masks_dir: Path,
    logs_dir: Path,
    generation_idx: int,
) -> List[str]:
    cmd: List[str] = [
        sys.executable,
        str(cfg.runner_script),
        "--ararl-dir", str(cfg.ararl_dir),
        "--logs-dir", str(logs_dir),
        "--gradle-root", str(cfg.gradle_root),
        "--work-root", str(cfg.work_root / f"ga_gen_{generation_idx + 1:03d}"),
        "--mask-file", str(masks_dir),
        "--nodes",
        *[str(n) for n in cfg.nodes],
        "--ppm",
        *[str(p) for p in cfg.ppms],
        "--traffic-seeds",
        *[str(s) for s in cfg.traffic_seeds],
        "--duration-sf", str(cfg.duration_sf),
        "--warmup-sf", str(cfg.warmup_sf),
        "--sim-seed", str(cfg.sim_seed),
        "--agent-seed", str(cfg.agent_seed),
        "--jobs", str(cfg.jobs),
        "--heartbeat-secs", str(cfg.heartbeat_secs),
        "--error-log-tail", str(cfg.error_log_tail),
    ]
    if cfg.topology_ids:
        cmd += ["--topology-ids", *cfg.topology_ids]
    else:
        cmd += ["--topologies", str(cfg.topologies)]
    if cfg.gradle_user_home is not None:
        cmd += ["--gradle-user-home", str(cfg.gradle_user_home)]
    if cfg.tx_range is not None:
        cmd += ["--tx-range", str(cfg.tx_range)]
    if cfg.int_range is not None:
        cmd += ["--int-range", str(cfg.int_range)]
    if cfg.keep_work:
        cmd.append("--keep-work")
    if cfg.dry_run:
        cmd.append("--dry-run")
    return cmd


def collect_scenarios_for_mask(cfg: GAConfig, logs_dir: Path, mid: str) -> List[Dict]:
    scenarios: List[Dict] = []
    for n in cfg.nodes:
        for ppm in cfg.ppms:
            jpath = logs_dir / f"N{n}_PPM{ppm}" / mid / "aggregated_results.json"
            if not jpath.is_file():
                continue
            try:
                obj = json.loads(jpath.read_text(encoding="utf-8"))
            except Exception:
                continue

            scenarios.append(
                {
                    "nodes": n,
                    "ppm": ppm,
                    "prr": _metric_mean(obj, "PRR"),
                    "nlt": _metric_mean(obj, "NLT"),
                    "delay": _metric_mean(obj, "Delay"),
                    "runs_total": _runs_count(obj, "total"),
                    "runs_valid": _runs_count(obj, "valid"),
                    "path": str(jpath),
                }
            )
    return scenarios


def cache_meta(cfg: GAConfig) -> Dict:
    return {
        "version": 2,
        "mask_file": str(cfg.mask_file),
        "search_features": cfg.search_space.search_features,
        "feature_order": cfg.search_space.feature_order,
        "allow_empty_mask": cfg.allow_empty_mask,
        "nodes": cfg.nodes,
        "ppms": cfg.ppms,
        "topologies": cfg.topologies,
        "topology_ids": cfg.topology_ids,
        "traffic_seeds": cfg.traffic_seeds,
        "duration_sf": cfg.duration_sf,
        "warmup_sf": cfg.warmup_sf,
        "sim_seed": cfg.sim_seed,
        "agent_seed": cfg.agent_seed,
        "tx_range": cfg.tx_range,
        "int_range": cfg.int_range,
        "weights": {"w_nlt": cfg.w_nlt, "w_prr": cfg.w_prr, "w_dly": cfg.w_dly},
        "delay_scale_ms": cfg.delay_scale_ms,
        "prr_min": cfg.prr_min,
        "lambda_stability": cfg.lambda_stability,
        "beta_prr_penalty": cfg.beta_prr_penalty,
        "gamma_missing_penalty": cfg.gamma_missing_penalty,
    }


def load_cache(path: Path, expected_meta: Dict) -> Dict[str, Dict]:
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}

    if not isinstance(raw, dict):
        return {}
    if raw.get("meta") != expected_meta:
        print("[INFO] Cache metadata mismatch; starting with empty cache.")
        return {}
    masks = raw.get("masks")
    if not isinstance(masks, dict):
        return {}
    return masks


def save_cache(path: Path, meta: Dict, masks: Dict[str, Dict]) -> None:
    write_json_atomic(path, {"meta": meta, "masks": masks})


def resume_meta(cfg: GAConfig, cache_meta_payload: Dict) -> Dict:
    return {
        "version": 2,
        "cache_meta": cache_meta_payload,
        "population": cfg.population,
        "elite": cfg.elite,
        "cx_rate": cfg.cx_rate,
        "mut_rate": cfg.mut_rate,
        "random_seed": cfg.random_seed,
    }


def _encode_rng_state(state: object) -> Optional[Dict[str, Any]]:
    # Persist RNG state in JSON-safe primitives (no pickle deserialization risk).
    if not isinstance(state, tuple) or len(state) != 3:
        return None
    version, internal_state, gauss_next = state
    if not isinstance(version, int):
        return None
    if not isinstance(internal_state, tuple):
        return None
    try:
        internal_list = [int(x) for x in internal_state]
    except Exception:
        return None
    if gauss_next is not None and not isinstance(gauss_next, (int, float)):
        return None
    return {
        "version": int(version),
        "internal_state": internal_list,
        "gauss_next": (float(gauss_next) if gauss_next is not None else None),
    }


def _decode_rng_state(value: object) -> Optional[Tuple[int, Tuple[int, ...], Optional[float]]]:
    # Legacy string values were pickle blobs; refuse to deserialize for safety.
    if isinstance(value, str):
        return None
    if not isinstance(value, dict):
        return None

    version = value.get("version")
    internal_raw = value.get("internal_state")
    gauss_next = value.get("gauss_next")
    if not isinstance(version, int):
        return None
    if not isinstance(internal_raw, list):
        return None
    if len(internal_raw) == 0 or len(internal_raw) > 10000:
        return None
    try:
        internal_state = tuple(int(x) for x in internal_raw)
    except Exception:
        return None
    if gauss_next is not None and not isinstance(gauss_next, (int, float)):
        return None

    return (
        int(version),
        internal_state,
        (float(gauss_next) if gauss_next is not None else None),
    )


def load_resume_state(path: Path, expected_meta: Dict) -> Optional[Dict]:
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(raw, dict):
        return None
    if raw.get("meta") != expected_meta:
        print("[INFO] Resume metadata mismatch; ignoring previous state.")
        return None
    state = raw.get("state")
    if not isinstance(state, dict):
        return None
    return state


def save_resume_state(path: Path, meta: Dict, state: Dict[str, Any]) -> None:
    write_json_atomic(path, {"meta": meta, "state": state})


def _normalize_population_from_state(
    raw_population: object,
    expected_size: int,
    num_features: int,
    allow_empty_mask: bool,
) -> Optional[List[List[int]]]:
    if not isinstance(raw_population, list):
        return None
    if len(raw_population) != expected_size:
        return None

    out: List[List[int]] = []
    for item in raw_population:
        if not isinstance(item, list):
            return None
        if len(item) != num_features:
            return None
        try:
            bits = [1 if int(x) != 0 else 0 for x in item]
        except Exception:
            return None
        if not valid_bits(bits, allow_empty_mask):
            return None
        out.append(bits)
    return out


def init_history_csv(path: Path) -> None:
    if path.is_file():
        return
    ensure_dir(path.parent)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "generation",
                "idx",
                "fitness",
                "mask_id",
                "mask_label",
                "bits",
                "selected_features",
                "prr_mean",
                "nlt_mean",
                "delay_mean",
                "found_scenarios",
                "expected_scenarios",
                "missing_fraction",
                "from_cache",
            ]
        )


def append_history_row(path: Path, row: List[object]) -> None:
    with path.open("a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(row)


def evaluate_generation(
    cfg: GAConfig,
    population: List[List[int]],
    generation_idx: int,
    cache: Dict[str, Dict],
) -> List[Tuple[List[int], float, Dict, bool]]:
    search_features = cfg.search_space.search_features
    expected_scenarios = len(cfg.nodes) * len(cfg.ppms)
    topology_count = len(cfg.topology_ids) if cfg.topology_ids else cfg.topologies
    expected_runs_per_scenario = max(1, topology_count * len(cfg.traffic_seeds))

    unique_by_mid: Dict[str, List[int]] = {}
    for bits in population:
        mid = mask_id(bits, search_features)
        unique_by_mid[mid] = bits

    cache_before = set(cache.keys())
    pending = [(mid, bits) for mid, bits in unique_by_mid.items() if mid not in cache]

    gen_root = cfg.ga_out / "runs" / f"gen_{generation_idx + 1:03d}"
    logs_dir = gen_root / "logs"
    masks_dir = gen_root / "masks"

    if pending:
        if cfg.resume:
            ensure_dir(gen_root)
            ensure_dir(logs_dir)
            recreate_dir(masks_dir)
        else:
            recreate_dir(gen_root)
            recreate_dir(logs_dir)
            recreate_dir(masks_dir)

        for mid, bits in pending:
            yml = build_submask_yaml(
                bits=bits,
                feature_order=cfg.search_space.feature_order,
                search_features=search_features,
                source_mask_name=cfg.mask_file.name,
            )
            (masks_dir / f"{mid}.yaml").write_text(yml, encoding="utf-8")

        cmd = runner_cmd_for_generation(cfg, masks_dir, logs_dir, generation_idx)
        rc = run_cmd(cmd)
        if rc != 0:
            print(f"[WARN] run.py exited non-zero for generation {generation_idx + 1}: rc={rc}")

        for mid, bits in pending:
            scenarios = collect_scenarios_for_mask(cfg, logs_dir, mid)
            fitness, detail = compute_fitness(cfg, scenarios, expected_scenarios, expected_runs_per_scenario)
            cache[mid] = {
                "mask_id": mid,
                "mask_label": mask_label(bits, search_features),
                "bits": list(bits),
                "selected_features": active_features(bits, search_features),
                "fitness": fitness,
                "detail": detail,
                "scenarios": scenarios,
                "generation_evaluated": generation_idx + 1,
            }

            summary_path = gen_root / "fitness" / f"{mid}.json"
            ensure_dir(summary_path.parent)
            summary_path.write_text(json.dumps(cache[mid], indent=2), encoding="utf-8")

    scored: List[Tuple[List[int], float, Dict, bool]] = []
    for bits in population:
        mid = mask_id(bits, search_features)
        info = cache.get(mid)
        if info is None:
            info = {
                "mask_id": mid,
                "mask_label": mask_label(bits, search_features),
                "bits": list(bits),
                "selected_features": active_features(bits, search_features),
                "fitness": -1e9,
                "detail": {"reason": "missing_cache_after_eval"},
                "scenarios": [],
            }
            cache[mid] = info
        scored.append((list(bits), float(info["fitness"]), info, (mid in cache_before)))

    return scored


def write_best_outputs(cfg: GAConfig, bits: List[int], info: Dict, generation_idx: int) -> None:
    best_dir = cfg.ga_out / "best"
    ensure_dir(best_dir)

    yaml_text = build_submask_yaml(
        bits=bits,
        feature_order=cfg.search_space.feature_order,
        search_features=cfg.search_space.search_features,
        source_mask_name=cfg.mask_file.name,
    )
    (best_dir / "best_mask.yaml").write_text(yaml_text, encoding="utf-8")

    payload = {
        "generation": generation_idx + 1,
        "mask_id": info.get("mask_id"),
        "mask_label": info.get("mask_label"),
        "selected_features": info.get("selected_features"),
        "fitness": info.get("fitness"),
        "detail": info.get("detail"),
        "scenarios": info.get("scenarios"),
    }
    (best_dir / "best_fitness.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")


def main() -> None:
    cfg = parse_args()
    random.seed(cfg.random_seed)

    ensure_dir(cfg.ga_out)
    ensure_dir(cfg.ga_out / "runs")
    ensure_dir(cfg.ga_out / "best")
    ensure_dir(cfg.work_root)

    print(f"[INFO] Searchable features ({len(cfg.search_space.search_features)}): {cfg.search_space.search_features}")
    print(f"[INFO] GA population={cfg.population}, generations={cfg.generations}, jobs={cfg.jobs}")
    print(f"[INFO] Fitness weights: NLT={cfg.w_nlt}, PRR={cfg.w_prr}, Delay={cfg.w_dly}")

    cache_path = cfg.ga_out / "cache.json"
    history_path = cfg.ga_out / "ga_history.csv"
    state_path = cfg.ga_out / "ga_state.json"

    meta = cache_meta(cfg)
    cache = load_cache(cache_path, meta)
    state_meta = resume_meta(cfg, meta)

    if not cfg.resume and history_path.is_file():
        history_path.unlink()
    init_history_csv(history_path)

    num_features = len(cfg.search_space.search_features)
    if num_features == 0 and not cfg.allow_empty_mask:
        die("No searchable features available and empty mask is not allowed.")

    population: List[List[int]] = []
    start_gen = 0
    best_bits: Optional[List[int]] = None
    best_fit = -1e18
    best_info: Dict = {}

    if cfg.resume:
        state = load_resume_state(state_path, state_meta)
        if state is None:
            print("[INFO] Resume requested but no compatible state found; starting from generation 1.")
        else:
            loaded_pop = _normalize_population_from_state(
                state.get("next_population"),
                expected_size=cfg.population,
                num_features=num_features,
                allow_empty_mask=cfg.allow_empty_mask,
            )
            if loaded_pop is None:
                print("[WARN] Saved resume population is invalid; starting from generation 1.")
            else:
                population = loaded_pop
                try:
                    start_gen = max(0, int(state.get("completed_generations", 0)))
                except Exception:
                    start_gen = 0

                raw_best_bits = state.get("best_bits")
                if isinstance(raw_best_bits, list) and len(raw_best_bits) == num_features:
                    try:
                        candidate = [1 if int(x) != 0 else 0 for x in raw_best_bits]
                        if valid_bits(candidate, cfg.allow_empty_mask):
                            best_bits = candidate
                    except Exception:
                        best_bits = None
                try:
                    best_fit = float(state.get("best_fit", -1e18))
                except Exception:
                    best_fit = -1e18
                if isinstance(state.get("best_info"), dict):
                    best_info = dict(state["best_info"])

                rng_blob = state.get("rng_state")
                if rng_blob is not None:
                    restored = _decode_rng_state(rng_blob)
                    if restored is None:
                        if isinstance(rng_blob, str):
                            print("[WARN] Ignoring legacy pickled RNG state in ga_state.json; continuation may diverge.")
                        else:
                            print("[WARN] Could not restore RNG state; continuation may diverge.")
                    else:
                        try:
                            random.setstate(restored)
                        except Exception:
                            print("[WARN] Could not restore RNG state; continuation may diverge.")

                print(
                    f"[INFO] Resuming at generation {start_gen + 1} "
                    f"(completed={start_gen}, target={cfg.generations})."
                )

    if not population:
        while len(population) < cfg.population:
            population.append(random_valid_bits(num_features, cfg.allow_empty_mask))

    if best_bits is None and cache:
        try:
            cached_best = max(cache.values(), key=lambda x: float(x.get("fitness", -1e18)))
            maybe_bits = cached_best.get("bits")
            maybe_fit = float(cached_best.get("fitness", -1e18))
            if isinstance(maybe_bits, list) and len(maybe_bits) == num_features:
                candidate = [1 if int(x) != 0 else 0 for x in maybe_bits]
                if valid_bits(candidate, cfg.allow_empty_mask):
                    best_bits = candidate
                    best_fit = maybe_fit
                    best_info = dict(cached_best)
        except Exception:
            pass

    if start_gen >= cfg.generations:
        print(
            f"[INFO] Completed generations in state ({start_gen}) already meet/exceed "
            f"--generations ({cfg.generations}); no new generations to run."
        )

    for gen in range(start_gen, cfg.generations):
        print(f"\n=== Generation {gen + 1}/{cfg.generations} ===")
        scored = evaluate_generation(cfg, population, gen, cache)
        scored.sort(key=lambda x: x[1], reverse=True)

        for idx, (bits, fit, info, from_cache) in enumerate(scored):
            d = info.get("detail", {})
            append_history_row(
                history_path,
                [
                    gen + 1,
                    idx,
                    f"{fit:.8f}",
                    info.get("mask_id", ""),
                    info.get("mask_label", ""),
                    "".join(str(int(b)) for b in bits),
                    ",".join(info.get("selected_features", [])),
                    d.get("prr_mean", ""),
                    d.get("nlt_mean", ""),
                    d.get("delay_mean", ""),
                    d.get("found_scenarios", ""),
                    d.get("expected_scenarios", ""),
                    d.get("missing_fraction", ""),
                    int(bool(from_cache)),
                ],
            )

        save_cache(cache_path, meta, cache)

        if scored and scored[0][1] > best_fit:
            best_bits = list(scored[0][0])
            best_fit = float(scored[0][1])
            best_info = dict(scored[0][2])
            write_best_outputs(cfg, best_bits, best_info, gen)
            print(
                f"[BEST] generation={gen + 1} fitness={best_fit:.6f} "
                f"mask={best_info.get('mask_label')} id={best_info.get('mask_id')}"
            )

        elite_count = min(cfg.elite, len(scored))
        next_pop: List[List[int]] = [list(scored[i][0]) for i in range(elite_count)]
        pool = [(list(bits), fit) for bits, fit, _info, _cached in scored]

        while len(next_pop) < cfg.population:
            if len(pool) == 1:
                child = mutate(pool[0][0], cfg.mut_rate, cfg.allow_empty_mask)
                next_pop.append(child)
                continue

            if random.random() < cfg.cx_rate:
                p1 = tournament_select(pool, k=3)
                p2 = tournament_select(pool, k=3)
                c1, c2 = crossover(p1, p2)
                c1 = mutate(c1, cfg.mut_rate, cfg.allow_empty_mask)
                c2 = mutate(c2, cfg.mut_rate, cfg.allow_empty_mask)
                if valid_bits(c1, cfg.allow_empty_mask):
                    next_pop.append(c1)
                if len(next_pop) < cfg.population and valid_bits(c2, cfg.allow_empty_mask):
                    next_pop.append(c2)
            else:
                p = tournament_select(pool, k=3)
                c = mutate(p, cfg.mut_rate, cfg.allow_empty_mask)
                if valid_bits(c, cfg.allow_empty_mask):
                    next_pop.append(c)

        population = next_pop[: cfg.population]
        save_resume_state(
            state_path,
            state_meta,
            {
                "completed_generations": gen + 1,
                "next_population": population,
                "best_bits": best_bits,
                "best_fit": best_fit,
                "best_info": best_info,
                "rng_state": _encode_rng_state(random.getstate()),
            },
        )

    print("\n=== DONE ===")
    if best_bits is None:
        print("No valid solution found.")
        return
    print(f"Best fitness: {best_fit:.6f}")
    print(f"Best mask   : {best_info.get('mask_label')} ({best_info.get('mask_id')})")
    print(f"Best YAML   : {cfg.ga_out / 'best' / 'best_mask.yaml'}")
    print(f"History CSV : {history_path}")
    print(f"Cache JSON  : {cache_path}")
    print(f"State JSON  : {state_path}")


if __name__ == "__main__":
    main()
