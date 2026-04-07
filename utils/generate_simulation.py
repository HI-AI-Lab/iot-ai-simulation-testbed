#!/usr/bin/env python3
"""
Generate COOJA topologies from a template — single **or** batch mode (hardened, article‑aligned).

What this script supports
- **Single-run mode (backward compatible):** You pass --out, --motes, --width/--height and it writes one
  `simulation-*.csc` plus matching `positions-*.h/.csv` and a SHA1.
- **Batch mode:** One command generates many topologies per node-count directly into
  `experiments/ararl/topologies/N{N}/simulation-nodes{N}-topo{TT}.csc` (and headers/CSVs/SHA1s),
  with optional auto W,H sizing from a target mean degree — or fixed article defaults.
- **Hardenings:** Separate seeds (placement vs sim), provenance in <description>, optional min-distance,
  connectivity check, degree-band check.

Article-aligned defaults (can be overridden via CLI):
- Area: 300 x 300 m
- UDGM ranges: tx_range=150, int_range=160
- Sink at edge-center (W/2, 0)

Example (batch):
  python3 /workspace/utils/generate_simulation.py \
    --template /workspace/utils/simulation_template.csc \
    --batch --nodes-list 60 80 100 --count 10 \
    --width 300 --height 300 \
    --tx_range 150 --int_range 160 \
    --topo-root /workspace/experiments/ararl/topologies \
    --seed-start 10001 --check-connected

Single-run (legacy) example:
  python3 /workspace/utils/generate_simulation.py \
    --template /workspace/utils/simulation_template.csc \
    --out /workspace/experiments/ararl/topologies/N60/simulation-nodes60-topo01.csc \
    --motes 60 --width 300 --height 300 \
    --placement-seed 10001 --sim-seed 10001 \
    --tx_range 150 --int_range 160 --check-connected

Notes
- Sink at (W/2, 0) by default; use --sink-at center to put at (W/2, H/2).
- [CONFIG_DIR]/simulation.js is left untouched.
"""
from __future__ import annotations

import argparse
import hashlib
import math
import random
from collections import deque
from pathlib import Path
from typing import Optional, Tuple
import xml.etree.ElementTree as ET

# ---------------- helpers ---------------- #

def _indent(elem, level: int = 0) -> None:
    i = "\n" + level * "  "
    if len(elem):
        if not (elem.text or "").strip():
            elem.text = i + "  "
        for e in elem:
            _indent(e, level + 1)
        if not (elem.tail or "").strip():
            elem.tail = i
    else:
        if level and not (elem.tail or "").strip():
            elem.tail = i


def _make_interface_config(parent, classname: str):
    ic = ET.SubElement(parent, "interface_config")
    ic.text = f"\n          {classname}\n          "
    return ic


def sha1_text(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()

# ------------- geometry checks (optional) ------------- #

def _pairwise_degree(pts, r: float):
    n = len(pts)
    deg = [0] * n
    rr = r * r
    for i in range(n):
        xi, yi = pts[i]
        for j in range(i + 1, n):
            dx = xi - pts[j][0]
            dy = yi - pts[j][1]
            if dx * dx + dy * dy <= rr:
                deg[i] += 1
                deg[j] += 1
    return deg


def _all_reachable_from_sink(pts, r: float) -> bool:
    # index 0 is sink
    n = len(pts)
    rr = r * r
    adj = [[] for _ in range(n)]
    for i in range(n):
        xi, yi = pts[i]
        for j in range(i + 1, n):
            dx = xi - pts[j][0]
            dy = yi - pts[j][1]
            if dx * dx + dy * dy <= rr:
                adj[i].append(j)
                adj[j].append(i)
    vis = [False] * n
    q = deque([0])
    vis[0] = True
    while q:
        u = q.popleft()
        for v in adj[u]:
            if not vis[v]:
                vis[v] = True
                q.append(v)
    return all(vis)

# ---------------- single-run core ---------------- #

def _generate_one(*, template: Path, out_path: Path, motes: int, width: float, height: float,
                  placement_seed: int, sim_seed: int, tx_range: float, int_range: float,
                  min_dist: float, max_tries: int, check_connected: bool,
                  target_degree: Optional[float], deg_tol: float, sink_at: str, title: str) -> None:
    rnd = random.Random(placement_seed)

    # Parse template
    tree = ET.parse(template)
    root = tree.getroot()

    simulation = root.find("simulation")
    if simulation is None:
        raise SystemExit("Template error: <simulation> not found")

    # Title & randomseed
    t = simulation.find("title")
    if t is not None:
        t.text = title
    rs = simulation.find("randomseed")
    if rs is not None:
        rs.text = str(sim_seed)

    # UDGM ranges
    rm = simulation.find("radiomedium")
    if rm is None:
        raise SystemExit("Template error: <radiomedium> missing")
    tx = rm.find("transmitting_range")
    if tx is not None:
        tx.text = str(tx_range)
    ir = rm.find("interference_range")
    if ir is not None:
        ir.text = str(int_range)

    # Append provenance to <description>
    desc = simulation.find("description")
    if desc is None:
        desc = ET.SubElement(simulation, "description")
    prov = (
        f"placement_seed={placement_seed}; sim_seed={sim_seed}; "
        f"W={width}; H={height}; tx={tx_range}; int={int_range}"
    )
    desc.text = (desc.text or "").rstrip() + ("\n" if desc.text else "") + prov + "\n"

    # Motetypes
    motetypes = simulation.findall("motetype")
    if len(motetypes) < 2:
        raise SystemExit("Template error: Need at least two <motetype> (sink + node)")
    sink_mt = motetypes[0]
    node_mt = motetypes[1]

    # Remove any existing <mote>
    for mt in (sink_mt, node_mt):
        for child in list(mt):
            if child.tag == "mote":
                mt.remove(child)

    # Sink position
    if sink_at == "edge":
        sink_x, sink_y = width / 2.0, 0.0
    else:
        sink_x, sink_y = width / 2.0, height / 2.0

    positions = [(sink_x, sink_y)]  # index 0 = sink

    # Add sink mote
    mote = ET.SubElement(sink_mt, "mote")
    pos_ic = _make_interface_config(mote, "org.contikios.cooja.interfaces.Position")
    pos = ET.SubElement(pos_ic, "pos")
    pos.set("x", f"{sink_x:.1f}")
    pos.set("y", f"{sink_y:.1f}")
    id_ic = _make_interface_config(mote, "org.contikios.cooja.contikimote.interfaces.ContikiMoteID")
    ET.SubElement(id_ic, "id").text = "1"

    # Helper: spacing check
    def ok_min_dist(x: float, y: float, pts, dmin: float) -> bool:
        if dmin <= 0:
            return True
        d2min = dmin * dmin
        for (px, py) in pts:
            dx = x - px
            dy = y - py
            if dx * dx + dy * dy < d2min:
                return False
        return True

    # Place non-sink motes
    for i in range(2, motes + 1):
        tries = 0
        while True:
            x = rnd.uniform(0.0, width)
            y = rnd.uniform(0.0, height)
            if ok_min_dist(x, y, positions, min_dist):
                break
            tries += 1
            if tries > max_tries:
                raise SystemExit(
                    f"Failed to place mote {i} with --min-dist={min_dist} after {max_tries} tries. "
                    "Use smaller --min-dist or larger area."
                )
        positions.append((x, y))

        mote = ET.SubElement(node_mt, "mote")
        pos_ic = _make_interface_config(mote, "org.contikios.cooja.interfaces.Position")
        pos = ET.SubElement(pos_ic, "pos")
        pos.set("x", f"{x:.1f}")
        pos.set("y", f"{y:.1f}")
        id_ic = _make_interface_config(mote, "org.contikios.cooja.contikimote.interfaces.ContikiMoteID")
        ET.SubElement(id_ic, "id").text = str(i)

    # Optional checks
    if check_connected:
        if not _all_reachable_from_sink(positions, tx_range):
            raise SystemExit("Connectivity check failed: not all nodes reach sink with given tx_range.")
    if target_degree is not None:
        deg = _pairwise_degree(positions, tx_range)
        mean_deg = sum(deg) / len(deg)
        lo = target_degree * (1.0 - deg_tol)
        hi = target_degree * (1.0 + deg_tol)
        if not (lo <= mean_deg <= hi):
            raise SystemExit(
                f"Mean degree {mean_deg:.2f} outside target band [{lo:.2f},{hi:.2f}]. "
                "Adjust area (W,H) or tx_range to hit the density you want."
            )

    # Write CSC
    out_path.parent.mkdir(parents=True, exist_ok=True)
    _indent(root)
    out_path.write_text(ET.tostring(root, encoding="unicode"), encoding="utf-8")
    print(
        f"Wrote {out_path} with {motes} motes in {width}x{height}, "
        f"placement_seed={placement_seed}, sim_seed={sim_seed}."
    )

    # Header + CSV + SHA1
    header_path = out_path.with_name(f"positions-{out_path.stem}.h")
    csv_path = out_path.with_name(f"positions-{out_path.stem}.csv")
    sha_path = out_path.with_name(f"placement-{out_path.stem}.sha1")

    guard = "POSITIONS_" + out_path.stem.upper().replace("-", "_") + "_H"
    xs = ", ".join(["0.0f"] + [f"{p[0]:.2f}f" for p in positions])
    ys = ", ".join(["0.0f"] + [f"{p[1]:.2f}f" for p in positions])

    header_path.write_text(
        f"/* Auto-generated positions header for {out_path.name} */\n"
        f"#ifndef {guard}\n#define {guard}\n\n"
        f"#define NUM_NODES {motes}\n\n"
        f"static const float node_pos_x[NUM_NODES+1] = {{ {xs} }};\n"
        f"static const float node_pos_y[NUM_NODES+1] = {{ {ys} }};\n\n"
        f"#endif /* {guard} */\n",
        encoding="utf-8",
    )

    # CSV & hash
    lines = ["id,x,y,is_sink"]
    for i, (x, y) in enumerate(positions, start=1):
        lines.append(f"{i},{x:.2f},{y:.2f},{1 if i==1 else 0}")
    csv_text = "\n".join(lines) + "\n"
    csv_path.write_text(csv_text, encoding="utf-8")
    sha = sha1_text("\n".join(lines[1:]))
    sha_path.write_text(sha + "\n", encoding="utf-8")
    print(f"Wrote {header_path}, {csv_path}, {sha_path} (hash={sha[:8]}...).")


# ---------------- CLI ---------------- #

def parse_args():
    p = argparse.ArgumentParser(
        description="Generate COOJA .csc (single or batch) with seeded positions + optional checks",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    # Common
    p.add_argument("--template", required=True, help="Path to CSC template")
    p.add_argument("--tx_range", type=float, default=150.0, help="UDGM transmitting range")
    p.add_argument("--int_range", type=float, default=160.0, help="UDGM interference range")
    p.add_argument("--min-dist", type=float, default=0.0, help="Minimum distance between any two motes (rejection sampling)")
    p.add_argument("--max-tries", type=int, default=10000, help="Max tries per mote for --min-dist placement")
    p.add_argument("--check-connected", action="store_true", help="Ensure all nodes are reachable from sink within tx_range")
    p.add_argument("--target-degree", type=float, help="Target mean degree given tx_range; if set, enforce band via --deg-tol")
    p.add_argument("--deg-tol", type=float, default=0.2, help="Tolerance for mean degree target (fraction)")
    p.add_argument("--sink-at", choices=["edge", "center"], default="edge", help="Sink placement: edge center vs geometric center")
    # Single-run (legacy)
    p.add_argument("--out", help="Output CSC path (single-run mode)")
    p.add_argument("--motes", type=int, help="Total motes INCLUDING sink (ID=1) [single-run]")
    p.add_argument("--width", type=float, help="Area width [single-run or batch override]")
    p.add_argument("--height", type=float, help="Area height [single-run or batch override]")
    p.add_argument("--title", default="My simulation", help="Simulation title [single-run]")
    p.add_argument("--seed", type=int, default=123456, help="Alias for --placement-seed [single-run]")
    p.add_argument("--placement-seed", type=int, help="Seed for topology placement (positions) [single-run]")
    p.add_argument("--sim-seed", type=int, help="Seed for COOJA <randomseed> (MAC/PHY) [single-run]")
    # Batch mode
    p.add_argument("--batch", action="store_true", help="Enable batch generation mode")
    p.add_argument("--nodes-list", type=int, nargs="*", help="List of N to generate in batch (e.g., 60 80 100)")
    p.add_argument("--count", type=int, default=10, help="How many topologies per N [batch]")
    p.add_argument("--topo-ids", nargs="*", help="Explicit topo IDs (e.g., 01 02) [batch]")
    p.add_argument("--seed-start", type=int, default=10001, help="Starting placement seed; seeds increment by 1 [batch]")
    p.add_argument("--seeds", type=int, nargs="*", help="Explicit placement seeds for each topo index [batch]")
    p.add_argument("--sim-seed-mode", choices=["same", "offset", "fixed"], default="same", help="How to set sim-seed in batch")
    p.add_argument("--sim-seed-offset", type=int, default=0, help="If sim-seed-mode=offset, sim_seed = placement_seed + offset")
    p.add_argument("--sim-seed-fixed", type=int, help="If sim-seed-mode=fixed, use this sim_seed for all")
    p.add_argument("--topo-root", type=Path, default=Path("experiments/ararl/topologies"), help="Where to write per-topology files [batch]")
    return p.parse_args()


# ---------------- main ---------------- #

def _compute_wh(n: int, r: float, k: float) -> Tuple[float, float]:
    """Random geometric graph estimate: E[degree] ≈ (N/A)*πr² ⇒ A ≈ Nπr²/k."""
    A = n * math.pi * (r ** 2) / k
    W = math.sqrt(A)
    return (W, W)


def main():
    args = parse_args()

    template = Path(args.template)
    if not template.is_file():
        raise SystemExit(f"Template not found: {template}")

    # Decide mode
    batch_mode = bool(args.batch or args.nodes_list)

    if not batch_mode:
        # ---- Single-run legacy path ----
        if not (args.out and args.motes and args.width and args.height):
            raise SystemExit("Single-run requires --out, --motes, --width, --height")
        placement_seed = args.placement_seed if args.placement_seed is not None else args.seed
        sim_seed = args.sim_seed if args.sim_seed is not None else placement_seed
        _generate_one(
            template=template,
            out_path=Path(args.out),
            motes=int(args.motes),
            width=float(args.width),
            height=float(args.height),
            placement_seed=int(placement_seed),
            sim_seed=int(sim_seed),
            tx_range=float(args.tx_range),
            int_range=float(args.int_range),
            min_dist=float(args.min_dist),
            max_tries=int(args.max_tries),
            check_connected=bool(args.check_connected),
            target_degree=float(args.target_degree) if args.target_degree is not None else None,
            deg_tol=float(args.deg_tol),
            sink_at=str(args.sink_at),
            title=str(args.title),
        )
        return

    # ---- Batch mode ----
    if not args.nodes_list:
        raise SystemExit("Batch mode needs --nodes-list (e.g., 60 80 100)")

    # Topology IDs
    if args.topo_ids:
        tids = [tid.zfill(2) for tid in args.topo_ids]
        if len(tids) != args.count:
            raise SystemExit("--topo-ids length must equal --count")
    else:
        tids = [f"{i:02d}" for i in range(1, args.count + 1)]

    # Seeds per index
    if args.seeds:
        if len(args.seeds) != args.count:
            raise SystemExit("--seeds length must equal --count")
        seeds = list(args.seeds)
    else:
        seeds = [args.seed_start + i for i in range(args.count)]

    # Sim-seed policy
    def sim_seed_for(ps: int) -> int:
        if args.sim_seed_mode == "same":
            return ps
        if args.sim_seed_mode == "offset":
            return ps + int(args.sim_seed_offset)
        if args.sim_seed_mode == "fixed":
            if args.sim_seed_fixed is None:
                raise SystemExit("--sim-seed-mode fixed requires --sim-seed-fixed")
            return int(args.sim_seed_fixed)
        raise SystemExit("Unknown --sim-seed-mode")

    # For each N
    for n in args.nodes_list:
        n = int(n)
        # Decide W,H for this N
        if args.width is not None and args.height is not None:
            W, H = float(args.width), float(args.height)
        else:
            # If user did not specify W,H: use article default 300x300.
            # If user provided --target-degree explicitly, allow auto-sizing instead.
            if args.target_degree is not None:
                W, H = _compute_wh(n, float(args.tx_range), float(args.target_degree))
            else:
                W, H = 300.0, 300.0
        out_dir = args.topo_root / f"N{n}"

        # Per topology index
        for idx in range(args.count):
            topo_id = tids[idx]
            ps = int(seeds[idx])
            ss = sim_seed_for(ps)
            out_path = out_dir / f"simulation-nodes{n}-topo{topo_id}.csc"
            title = f"N{n}_topo{topo_id}"

            _generate_one(
                template=template,
                out_path=out_path,
                motes=n,
                width=W,
                height=H,
                placement_seed=ps,
                sim_seed=ss,
                tx_range=float(args.tx_range),
                int_range=float(args.int_range),
                min_dist=float(args.min_dist),
                max_tries=int(args.max_tries),
                check_connected=bool(args.check_connected),
                target_degree=float(args.target_degree) if args.target_degree is not None else None,
                deg_tol=float(args.deg_tol),
                sink_at=str(args.sink_at),
                title=title,
            )

    print("All requested topologies generated.")


if __name__ == "__main__":
    main()
