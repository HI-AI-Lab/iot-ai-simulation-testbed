#!/usr/bin/env python3
"""
Genetic search for feature masks (GA) on top of run_parallel.py

- Each chromosome toggles feature booleans in the YAML:
    features:
      all: false
      etx: true/false
      ...
- For each mask, we call run_parallel.py with your full test matrix.
- We read aggregated_results.json from each (N, PPM) and produce a scalar fitness
  based only on PRR, NLT, E2E, QLR (paper metrics).
- Caching avoids re-evaluating the same mask.
- GA evaluates masks sequentially; run_parallel.py does the parallelism using --runner-jobs.

Example:
  python3 ga_search.py \
    --ararl-dir /workspace/testbed/experiments/ararl \
    --gradle-root /workspace/contiki-ng/tools/cooja \
    --work-root /workspace/testbed/_work \
    --ga-out   /workspace/testbed/ga_out \
    --nodes 60 80 100 --ppm 80 100 120 --topologies 10 --traffic-seeds 1 \
    --features etx,hc,re,qlr,rssi,bdi,wr,pc,si,gen,fwd,qloss \
    --require-at-least-one etx,hc,re \
    --population 16 --generations 12 --elite 2 --cx-rate 0.8 --mut-rate 0.08 \
    --runner-jobs 0 --prr-min 0.85
"""

from __future__ import annotations
import argparse, json, math, os, random, shutil, string, subprocess, sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Tuple, Optional

# ------------------------ CLI & Config ------------------------

@dataclass
class GAConfig:
    # Runner wiring
    ararl_dir: Path
    gradle_root: Path
    work_root: Path
    ga_out: Path
    nodes: List[int]
    ppms: List[int]
    topology_ids: List[str]
    traffic_seeds: List[int]
    duration_sf: int
    warmup_sf: int
    sim_seed: int
    agent_seed: int
    tx_range: Optional[float]
    int_range: Optional[float]
    runner_jobs: int  # passed to run_parallel.py --jobs (0=auto)

    # GA search space
    features: List[str]
    require_at_least_one: List[str]  # any of these must be True; empty => no constraint

    # GA parameters
    population: int
    generations: int
    elite: int
    cx_rate: float
    mut_rate: float
    random_seed: int

    # Fitness weights/scales
    w_prr: float
    w_nlt: float
    w_e2e: float
    w_qlr: float
    e2e_scale_ms: float
    qlr_scale: float
    prr_min: float  # hard cutoff (penalize if below)
    # nlt normalization uses duration_sf*1000 automatically

def parse_args() -> GAConfig:
    ap = argparse.ArgumentParser(
        description="Genetic algorithm to search feature masks (calls run_parallel.py).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    # Runner wiring
    ap.add_argument("--ararl-dir", type=Path, required=True)
    ap.add_argument("--gradle-root", type=Path, required=True)
    ap.add_argument("--work-root", type=Path, default=Path("testbed/_work"))
    ap.add_argument("--ga-out", type=Path, default=Path("testbed/ga_out"))
    ap.add_argument("--nodes", type=int, nargs="+", default=[60,80,100])
    ap.add_argument("--ppm", type=int, nargs="+", default=[80,100,120])
    ap.add_argument("--topologies", type=int, default=10)
    ap.add_argument("--topology-ids", type=str, nargs="*")
    ap.add_argument("--traffic-seeds", type=int, nargs="+", default=[1])
    ap.add_argument("--duration-sf", type=int, default=180)
    ap.add_argument("--warmup-sf", type=int, default=12)
    ap.add_argument("--sim-seed", type=int, default=67890)
    ap.add_argument("--agent-seed", type=int, default=12345)
    ap.add_argument("--tx-range", type=float, default=None)
    ap.add_argument("--int-range", type=float, default=None)
    ap.add_argument("--runner-jobs", type=int, default=0, help="forwarded to run_parallel.py --jobs")

    # GA search space
    ap.add_argument("--features", type=str, required=True,
                    help="Comma-separated feature keys (e.g., etx,qlr,re,...)")
    ap.add_argument("--require-at-least-one", type=str, default="",
                    help="Comma-separated set; enforce at least one True among these")

    # GA parameters
    ap.add_argument("--population", type=int, default=16)
    ap.add_argument("--generations", type=int, default=12)
    ap.add_argument("--elite", type=int, default=2)
    ap.add_argument("--cx-rate", type=float, default=0.8)
    ap.add_argument("--mut-rate", type=float, default=0.08)
    ap.add_argument("--random-seed", type=int, default=1337)

    # Fitness & scales
    ap.add_argument("--w-prr", type=float, default=0.4)
    ap.add_argument("--w-nlt", type=float, default=0.3)
    ap.add_argument("--w-e2e", type=float, default=0.2)
    ap.add_argument("--w-qlr", type=float, default=0.1)
    ap.add_argument("--e2e-scale-ms", type=float, default=1000.0)
    ap.add_argument("--qlr-scale", type=float, default=0.05)
    ap.add_argument("--prr-min", type=float, default=0.85)

    a = ap.parse_args()

    # Topology IDs
    if a.topology_ids:
        topo_ids = a.topology_ids
    else:
        width = len(str(a.topologies))
        topo_ids = [str(i).zfill(width) for i in range(1, a.topologies + 1)]

    features = [s.strip() for s in a.features.split(",") if s.strip()]
    require_any = [s.strip() for s in a.require_at_least_one.split(",") if s.strip()]

    return GAConfig(
        ararl_dir=a.ararl_dir.resolve(),
        gradle_root=a.gradle_root.resolve(),
        work_root=a.work_root.resolve(),
        ga_out=a.ga_out.resolve(),
        nodes=a.nodes,
        ppms=a.ppm,
        topology_ids=topo_ids,
        traffic_seeds=a.traffic_seeds,
        duration_sf=a.duration_sf,
        warmup_sf=a.warmup_sf,
        sim_seed=a.sim_seed,
        agent_seed=a.agent_seed,
        tx_range=a.tx_range,
        int_range=a.int_range,
        runner_jobs=a.runner_jobs,
        features=features,
        require_at_least_one=require_any,
        population=a.population,
        generations=a.generations,
        elite=a.elite,
        cx_rate=a.cx_rate,
        mut_rate=a.mut_rate,
        random_seed=a.random_seed,
        w_prr=a.w_prr,
        w_nlt=a.w_nlt,
        w_e2e=a.w_e2e,
        w_qlr=a.w_qlr,
        e2e_scale_ms=a.e2e_scale_ms,
        qlr_scale=a.qlr_scale,
        prr_min=a.prr_min,
    )

# ------------------------ Utilities ------------------------

def ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)

def run_cmd(cmd: List[str], cwd: Optional[Path] = None, env: Optional[Dict[str,str]] = None) -> int:
    print("[RUN]", " ".join(cmd), f"(cwd={cwd})")
    try:
        proc = subprocess.Popen(cmd, cwd=str(cwd) if cwd else None, env=env)
        return proc.wait()
    except FileNotFoundError:
        return 127

def mask_to_id(bits: List[int], features: List[str]) -> str:
    on = [f for b, f in zip(bits, features) if b == 1]
    if not on:
        return "none"
    return "-".join(on)

def build_mask_yaml(bits: List[int], features: List[str], mask_id: str) -> str:
    lines = []
    lines.append("run:")
    lines.append(f"  id: {mask_id}")
    lines.append(f"  notes: GA auto-generated mask")
    lines.append("")
    lines.append("features:")
    lines.append("  all: false")
    for b, feat in zip(bits, features):
        val = "true" if b == 1 else "false"
        lines.append(f"  {feat}: {val}")
    return "\n".join(lines) + "\n"

def valid_mask(bits: List[int], required_any: List[str], features: List[str]) -> bool:
    if not required_any:
        return True
    # At least one of required_any must be True
    for name in required_any:
        if name in features:
            idx = features.index(name)
            if bits[idx] == 1:
                return True
    return False

def random_bits(n: int) -> List[int]:
    return [1 if random.random() < 0.5 else 0 for _ in range(n)]

def mutate(bits: List[int], rate: float) -> List[int]:
    out = bits[:]
    for i in range(len(out)):
        if random.random() < rate:
            out[i] = 1 - out[i]
    return out

def crossover(a: List[int], b: List[int]) -> Tuple[List[int], List[int]]:
    if len(a) < 2:
        return a[:], b[:]
    cut = random.randint(1, len(a)-1)
    c1 = a[:cut] + b[cut:]
    c2 = b[:cut] + a[cut:]
    return c1, c2

def tournament_select(pop: List[Tuple[List[int], float]], k: int = 3) -> List[int]:
    # pop: list of (bits, fitness)
    cand = random.sample(pop, min(k, len(pop)))
    cand.sort(key=lambda x: x[1], reverse=True)
    return cand[0][0][:]

# ------------------------ Evaluation ------------------------

def eval_mask(cfg: GAConfig, bits: List[int], cache: Dict[str, Dict]) -> Tuple[float, Dict]:
    """Write YAML, call run_parallel.py, read aggregated JSONs, compute fitness."""
    mask_id = mask_to_id(bits, cfg.features)
    if mask_id in cache:
        return cache[mask_id]["fitness"], cache[mask_id]

    if not valid_mask(bits, cfg.require_at_least_one, cfg.features):
        # Invalid per constraint -> harsh penalty
        res = {"mask_id": mask_id, "invalid": True, "fitness": -1e6}
        cache[mask_id] = res
        return res["fitness"], res

    # Prepare dirs
    run_root = cfg.ga_out / "runs" / mask_id
    ensure_dir(run_root)
    mask_path = run_root / "mask.yaml"
    ensure_dir(mask_path.parent)
    # Write YAML
    mask_yaml = build_mask_yaml(bits, cfg.features, mask_id)
    mask_path.write_text(mask_yaml, encoding="utf-8")

    # Call run_parallel.py (sequential GA; runner does internal parallelism)
    cmd = [
        "python3", "run_parallel.py",
        "--ararl-dir", str(cfg.ararl_dir),
        "--logs-dir",  str(run_root),
        "--gradle-root", str(cfg.gradle_root),
        "--work-root", str(cfg.work_root),
        "--nodes", *[str(n) for n in cfg.nodes],
        "--ppm", *[str(p) for p in cfg.ppms],
        "--topology-ids", *cfg.topology_ids,
        "--mask-file", str(mask_path),
        "--mask-name", mask_id,
        "--duration-sf", str(cfg.duration_sf),
        "--warmup-sf",  str(cfg.warmup_sf),
        "--sim-seed",   str(cfg.sim_seed),
        "--agent-seed", str(cfg.agent_seed),
        "--traffic-seeds", *[str(s) for s in cfg.traffic_seeds],
        "--jobs", str(cfg.runner_jobs),
    ]
    if cfg.tx_range is not None:
        cmd += ["--tx-range", str(cfg.tx_range)]
    if cfg.int_range is not None:
        cmd += ["--int-range", str(cfg.int_range)]

    rc = run_cmd(cmd)
    # Even if rc!=0, try to read whatever results exist and penalize if broken.

    # Collect scenario JSONs
    scenarios: List[Dict] = []
    for n in cfg.nodes:
        for p in cfg.ppms:
            jpath = run_root / f"N{n}_PPM{p}" / mask_id / "aggregated_results.json"
            if jpath.is_file():
                try:
                    scenarios.append(json.loads(jpath.read_text(encoding="utf-8")))
                except Exception:
                    pass

    # Compute fitness
    fitness, detail = compute_fitness(cfg, scenarios)
    res = {
        "mask_id": mask_id,
        "bits": bits,
        "scenarios": scenarios,
        "fitness": fitness,
        "detail": detail,
        "return_code": rc,
    }
    cache[mask_id] = res
    # Persist a per-mask summary
    (run_root / "fitness.json").write_text(json.dumps(res, indent=2), encoding="utf-8")
    return fitness, res

def _metric(m: Dict, name: str) -> Optional[float]:
    # aggregated_results.json -> metrics.name.mean
    try:
        return float(m["metrics"][name]["mean"])
    except Exception:
        return None

def compute_fitness(cfg: GAConfig, scenarios: List[Dict]) -> Tuple[float, Dict]:
    """Combine scenario metrics with scalarization; penalize clearly bad results."""
    if not scenarios:
        return -1e6, {"reason": "no_scenarios"}

    # Average across scenarios (simple mean of means)
    prr_vals, nlt_vals, e2e_vals, qlr_vals = [], [], [], []
    for sc in scenarios:
        prr = _metric(sc, "PRR");  nlt = _metric(sc, "NLT")
        e2e = _metric(sc, "E2E");  qlr = _metric(sc, "QLR")
        if prr is not None: prr_vals.append(prr)
        if nlt is not None: nlt_vals.append(nlt)
        if e2e is not None: e2e_vals.append(e2e)
        if qlr is not None: qlr_vals.append(qlr)

    # If too sparse, penalize
    if not prr_vals:
        return -1e6, {"reason": "missing_prr"}
    prr_mean = sum(prr_vals)/len(prr_vals)
    nlt_mean = sum(nlt_vals)/len(nlt_vals) if nlt_vals else 0.0
    e2e_mean = sum(e2e_vals)/len(e2e_vals) if e2e_vals else 1e9
    qlr_mean = sum(qlr_vals)/len(qlr_vals) if qlr_vals else 1.0

    # Guardrail: PRR minimum
    if prr_mean < cfg.prr_min:
        penalty = -1e5 * (cfg.prr_min - prr_mean + 0.01)
        return penalty, {"prr_mean": prr_mean, "penalty": "prr_below_min"}

    # Scales
    nlt_scale = max(1.0, cfg.duration_sf * 1000.0)
    e2e_score = 1.0 / (1.0 + e2e_mean / max(1.0, cfg.e2e_scale_ms))
    qlr_score = 1.0 / (1.0 + qlr_mean / max(1e-9, cfg.qlr_scale))
    nlt_score = nlt_mean / nlt_scale  # 0..>1 typically

    fitness = (cfg.w_prr * prr_mean) + (cfg.w_nlt * nlt_score) + (cfg.w_e2e * e2e_score) + (cfg.w_qlr * qlr_score)
    detail = {
        "prr_mean": prr_mean,
        "nlt_mean": nlt_mean,
        "e2e_mean": e2e_mean,
        "qlr_mean": qlr_mean,
        "nlt_score": nlt_score,
        "e2e_score": e2e_score,
        "qlr_score": qlr_score,
    }
    return fitness, detail

# ------------------------ GA main loop ------------------------

def main() -> None:
    cfg = parse_args()
    random.seed(cfg.random_seed)

    ensure_dir(cfg.ga_out)
    ensure_dir(cfg.ga_out / "runs")
    ensure_dir(cfg.ga_out / "best")

    # Cache of evaluated masks
    cache_path = cfg.ga_out / "cache.json"
    cache: Dict[str, Dict] = {}
    if cache_path.exists():
        try:
            cache = json.loads(cache_path.read_text(encoding="utf-8"))
        except Exception:
            cache = {}

    # CSV log
    hist_path = cfg.ga_out / "ga_history.csv"
    if not hist_path.exists():
        hist_path.write_text("generation,idx,fitness,mask_id,bits,prr,e2e,qlr,nlt\n", encoding="utf-8")

    # Initial population
    pop: List[List[int]] = []
    while len(pop) < cfg.population:
        b = random_bits(len(cfg.features))
        if valid_mask(b, cfg.require_at_least_one, cfg.features):
            pop.append(b)

    best_overall: Tuple[Optional[List[int]], float, Dict] = (None, -1e9, {})

    for gen in range(cfg.generations):
        print(f"\n=== Generation {gen+1}/{cfg.generations} ===")
        scored: List[Tuple[List[int], float, Dict]] = []

        for i, bits in enumerate(pop):
            fit, info = eval_mask(cfg, bits, cache)
            scored.append((bits, fit, info))
            # Append to CSV
            prr = info.get("detail", {}).get("prr_mean", "")
            e2e = info.get("detail", {}).get("e2e_mean", "")
            qlr = info.get("detail", {}).get("qlr_mean", "")
            nlt = info.get("detail", {}).get("nlt_mean", "")
            hist_path.write_text(
                (hist_path.read_text(encoding="utf-8") if hist_path.exists() else "generation,idx,fitness,mask_id,bits,prr,e2e,qlr,nlt\n")
                + f"{gen},{i},{fit:.6f},{info['mask_id']},{''.join(map(str,bits))},{prr},{e2e},{qlr},{nlt}\n",
                encoding="utf-8"
            )

        # Save cache
        cache_path.write_text(json.dumps(cache, indent=2), encoding="utf-8")

        # Sort and update global best
        scored.sort(key=lambda x: x[1], reverse=True)
        if scored[0][1] > best_overall[1]:
            best_overall = (scored[0][0][:], scored[0][1], scored[0][2])
            # Write best YAML
            best_bits = best_overall[0]
            best_id = mask_to_id(best_bits, cfg.features)
            best_yaml = build_mask_yaml(best_bits, cfg.features, best_id)
            (cfg.ga_out / "best" / "best_mask.yaml").write_text(best_yaml, encoding="utf-8")
            (cfg.ga_out / "best" / "best_fitness.json").write_text(json.dumps(best_overall[2], indent=2), encoding="utf-8")
            print(f"[BEST] gen {gen} fitness={best_overall[1]:.6f} mask={best_id}")

        # Elitism
        next_pop: List[List[int]] = [scored[i][0][:] for i in range(min(cfg.elite, len(scored)))]

        # Generate rest by selection + crossover + mutation
        pool = [(b, f) for (b, f, _info) in scored]
        while len(next_pop) < cfg.population:
            if random.random() < cfg.cx_rate and len(pool) >= 2:
                p1 = tournament_select(pool, k=3)
                p2 = tournament_select(pool, k=3)
                c1, c2 = crossover(p1, p2)
                c1 = mutate(c1, cfg.mut_rate)
                c2 = mutate(c2, cfg.mut_rate)
                # Enforce validity constraints
                if valid_mask(c1, cfg.require_at_least_one, cfg.features):
                    next_pop.append(c1)
                if len(next_pop) < cfg.population and valid_mask(c2, cfg.require_at_least_one, cfg.features):
                    next_pop.append(c2)
            else:
                p = tournament_select(pool, k=3)
                c = mutate(p, cfg.mut_rate)
                if valid_mask(c, cfg.require_at_least_one, cfg.features):
                    next_pop.append(c)

        pop = next_pop[:cfg.population]

    # Final best
    bbits, bfit, binfo = best_overall
    best_id = mask_to_id(bbits, cfg.features)
    print("\n=== DONE ===")
    print(f"Best fitness: {bfit:.6f}")
    print(f"Best mask   : {best_id}")
    print(f"Best YAML   : {cfg.ga_out/'best'/'best_mask.yaml'}")

if __name__ == "__main__":
    main()
