#!/usr/bin/env python3
"""
Generate a COOJA .csc from your template.

- Keeps [CONFIG_DIR]/simulation.js as-is (does NOT change <scriptfile>)
- Places N motes with random (x,y) in a WxH area (seeded)
- Updates simple fields (title, seed, UDGM ranges, description)
- Writes Position as: <pos x="..." y="..."/>

Usage:
  python3 generate_simulation.py \
    --template /path/to/simulation_template.csc \
    --out      /path/to/simulation_gen.csc \
    --motes 20 --width 300 --height 300 --seed 123456
"""
import argparse
import random
from pathlib import Path
import xml.etree.ElementTree as ET

def _indent(elem, level=0):
    """Pretty-print XML in a COOJA-friendly way."""
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
        description="Generate COOJA .csc (uses <pos x= y=>; keeps [CONFIG_DIR]/simulation.js)."
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

    tree = ET.parse(tpl_path)
    root = tree.getroot()

    # Structure checks
    simulation = root.find("simulation")
    if simulation is None:
        raise SystemExit("Template error: <simulation> not found")

    # Update simple fields
    t = simulation.find("title")
    if t is not None:
        t.text = args.title
    rs = simulation.find("randomseed")
    if rs is not None:
        rs.text = str(args.seed)

    rm = simulation.find("radiomedium")
    if rm is None:
        raise SystemExit("Template error: <radiomedium> missing")
    tx = rm.find("transmitting_range")
    if tx is not None:
        tx.text = str(args.tx_range)
    ir = rm.find("interference_range")
    if ir is not None:
        ir.text = str(args.int_range)
        
    motetypes = simulation.findall("motetype")
    if len(motetypes) < 2:
        raise SystemExit("Template error: Need at least two <motetype> (sink + node)")

    sink_mt = motetypes[0]         # leave untouched
    node_mt = motetypes[1]         # generate motes here

    rnd = random.Random(args.seed)
    SINK_X = args.width / 2.0
    SINK_Y = 0.0
    
    mote = ET.SubElement(sink_mt, "mote")
    pos_ic = _make_interface_config(mote, "org.contikios.cooja.interfaces.Position")
    x, y = SINK_X, SINK_Y
    pos = ET.SubElement(pos_ic, "pos")
    pos.set("x", f"{x:.1f}")
    pos.set("y", f"{y:.1f}")
    
    id_ic = _make_interface_config(mote, "org.contikios.cooja.contikimote.interfaces.ContikiMoteID")
    ET.SubElement(id_ic, "id").text = str(1)

    # Remove any existing <mote> entries (replace the template placeholder)
    for child in list(node_mt):
        if child.tag == "mote":
            node_mt.remove(child)

    # Generate motes using <pos x=".." y=".."/>; fix sink (ID=1) at edge-center
    rnd = random.Random(args.seed)
    SINK_X = args.width / 2.0
    SINK_Y = 0.0
    for i in range(2, args.motes+1):
        mote = ET.SubElement(node_mt, "mote")

        pos_ic = _make_interface_config(mote, "org.contikios.cooja.interfaces.Position")
        x = rnd.uniform(0.0, args.width)
        y = rnd.uniform(0.0, args.height)
        pos = ET.SubElement(pos_ic, "pos")
        pos.set("x", f"{x:.1f}")
        pos.set("y", f"{y:.1f}")

        id_ic = _make_interface_config(mote, "org.contikios.cooja.contikimote.interfaces.ContikiMoteID")
        ET.SubElement(id_ic, "id").text = str(i)

    # Do NOT touch the ScriptRunner plugin; [CONFIG_DIR]/simulation.js remains.

    _indent(root)
    out_path.write_text(ET.tostring(root, encoding="unicode"), encoding="utf-8")
    print(f"Wrote {out_path} with {args.motes} motes in {args.width}x{args.height}, seed={args.seed}.")

if __name__ == "__main__":
    main()
