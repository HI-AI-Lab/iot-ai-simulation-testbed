#!/usr/bin/env python3
"""
Generate a COOJA .csc from your template.

- Keeps [CONFIG_DIR]/simulation.js as-is (does NOT change <scriptfile>)
- Puts N motes with random (x,y) in a WxH area (seeded for reproducibility)
- Updates simple fields (title, seed, UDGM ranges, description)
- Writes Position as: <pos x="..." y="..."/>

Usage example:
  python3 generate_simulation.py \
    --template /workspace/testbed/experiments/of0/rpl/simulation_template.csc \
    --out      /workspace/testbed/experiments/of0/rpl/simulation_gen.csc \
    --motes 20 --width 300 --height 300 --seed 123456 \
    --tx_range 50 --int_range 100 \
    --title "My simulation" \
    --motetype_desc "Cooja Mote Type #1"
"""
import argparse
import random
from pathlib import Path
import xml.etree.ElementTree as ET

def _indent(elem, level=0):
    """Pretty-print XML (compact, COOJA-friendly)."""
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
    """Create <interface_config> with the class name as text (COOJA style)."""
    ic = ET.SubElement(parent, "interface_config")
    ic.text = f"\n          {classname}\n          "
    return ic

def parse_args():
    p = argparse.ArgumentParser(
        description="Generate a COOJA .csc with N random motes (uses <pos x= y=>; keeps [CONFIG_DIR]/simulation.js)."
    )
    p.add_argument("--template", required=True, help="Path to CSC template")
    p.add_argument("--out", required=True, help="Output CSC path")
    p.add_argument("--motes", type=int, default=20, help="Number of motes")
    p.add_argument("--width", type=float, default=300.0, help="Area width")
    p.add_argument("--height", type=float, default=300.0, help="Area height")
    p.add_argument("--seed", type=int, default=123456, help="Random seed")
    p.add_argument("--title", default="My simulation", help="Simulation title")
    p.add_argument("--tx_range", type=float, default=50.0, help="UDGM transmitting range")
    p.add_argument("--int_range", type=float, default=100.0, help="UDGM interference range")
    p.add_argument("--motetype_desc", default="Cooja Mote Type #1", help="Mote type description")
    return p.parse_args()

def main():
    args = parse_args()

    tpl_path = Path(args.template)
    out_path = Path(args.out)
    if not tpl_path.is_file():
        raise SystemExit(f"Template not found: {tpl_path}")

    # Parse template
    tree = ET.parse(tpl_path)
    root = tree.getroot()

    # Structure checks
    simulation = root.find("simulation")
    if simulation is None:
        raise SystemExit("Template error: <simulation> not found")
    motetype = simulation.find("motetype")
    if motetype is None:
        raise SystemExit("Template error: <motetype> must be inside <simulation>")

    # Update simple fields
    title = simulation.find("title")
    if title is not None:
        title.text = args.title

    randomseed = simulation.find("randomseed")
    if randomseed is not None:
        randomseed.text = str(args.seed)

    radiomedium = simulation.find("radiomedium")
    if radiomedium is None:
        raise SystemExit("Template error: <radiomedium> missing")
    tx = radiomedium.find("transmitting_range")
    if tx is not None:
        tx.text = str(args.tx_range)
    intr = radiomedium.find("interference_range")
    if intr is not None:
        intr.text = str(args.int_range)

    desc = motetype.find("description")
    if desc is not None:
        desc.text = args.motetype_desc

    # Remove any existing <mote> children (the template's placeholder will be replaced)
    for child in list(motetype):
        if child.tag == "mote":
            motetype.remove(child)

    # Add N motes with random positions (using <pos x=".." y=".."/>)
    rnd = random.Random(args.seed)
    for i in range(1, args.motes + 1):
        mote = ET.SubElement(motetype, "mote")

        # Position
        pos_ic = _make_interface_config(mote, "org.contikios.cooja.interfaces.Position")
        x = rnd.uniform(0.0, args.width)
        y = rnd.uniform(0.0, args.height)
        pos = ET.SubElement(pos_ic, "pos")
        pos.set("x", f"{x:.8f}")
        pos.set("y", f"{y:.8f}")

        # Mote ID
        id_ic = _make_interface_config(mote, "org.contikios.cooja.contikimote.interfaces.ContikiMoteID")
        ET.SubElement(id_ic, "id").text = str(i)

    # Do NOT touch the ScriptRunner plugin block; we keep [CONFIG_DIR]/simulation.js.

    _indent(root)
    out_path.write_text(ET.tostring(root, encoding="unicode"), encoding="utf-8")
    print(f"Wrote {out_path} with {args.motes} motes in {args.width}x{args.height}, seed={args.seed}.")

if __name__ == "__main__":
    main()
