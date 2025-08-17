#!/usr/bin/env python3
import argparse
import random
import sys
from pathlib import Path
import xml.etree.ElementTree as ET

def indent(elem, level=0):
    # Pretty-print XML
    i = "\n" + level*"  "
    if len(elem):
        if not elem.text or not elem.text.strip():
            elem.text = i + "  "
        for e in elem:
            indent(e, level+1)
        if not elem.tail or not elem.tail.strip():
            elem.tail = i
    else:
        if level and (not elem.tail or not elem.tail.strip()):
            elem.tail = i

def parse_args():
    p = argparse.ArgumentParser(description="Generate a COOJA .csc with N random motes.")
    p.add_argument("--template", required=True, help="Path to CSC template")
    p.add_argument("--out", required=True, help="Output CSC path")
    p.add_argument("--scriptfile", required=True, help="Absolute path to simulation.js inside the container")
    p.add_argument("--motes", type=int, default=3, help="Number of motes")
    p.add_argument("--width", type=float, default=300.0, help="Area width for random positions")
    p.add_argument("--height", type=float, default=300.0, help="Area height for random positions")
    p.add_argument("--seed", type=int, default=123456, help="Random seed for reproducibility")
    p.add_argument("--title", default="My simulation", help="Simulation title")
    p.add_argument("--tx_range", type=float, default=50.0, help="UDGM transmitting range")
    p.add_argument("--int_range", type=float, default=100.0, help="UDGM interference range")
    p.add_argument("--motetype_desc", default="Cooja Mote Type #1", help="Mote type description")
    return p.parse_args()

def ensure_abs(p: str) -> str:
    q = Path(p)
    if not q.is_absolute():
        raise SystemExit(f"--scriptfile must be an absolute path (got: {p})")
    return str(q)

def main():
    args = parse_args()

    tpl_path = Path(args.template)
    out_path = Path(args.out)
    script_path = ensure_abs(args.scriptfile)

    if not tpl_path.is_file():
        raise SystemExit(f"Template not found: {tpl_path}")
    # script file might be generated later; don't hard-fail if missing on host

    tree = ET.parse(tpl_path)
    root = tree.getroot()

    # Basic structure checks
    simulation = root.find("simulation")
    if simulation is None:
        raise SystemExit("Template error: <simulation> not found")

    motetype = simulation.find("motetype")
    if motetype is None:
        raise SystemExit("Template error: <motetype> must be inside <simulation>")

    # Set simple fields if present
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

    # Clear any existing <mote> elements
    to_remove = [child for child in list(motetype) if child.tag == "mote"]
    for child in to_remove:
        motetype.remove(child)

    rnd = random.Random(args.seed)

    # Helper to create interface_config with class name and children
    def make_interface_config(parent, classname: str):
        ic = ET.SubElement(parent, "interface_config")
        # put the class name as a text node, keeping Cooja's expected style
        ic.text = f"\n          {classname}\n          "
        return ic

    # Build motes
    for i in range(1, args.motes + 1):
        mote = ET.SubElement(motetype, "mote")

        # Position
        pos_ic = make_interface_config(mote, "org.contikios.cooja.interfaces.Position")
        x = rnd.uniform(0.0, args.width)
        y = rnd.uniform(0.0, args.height)
        ET.SubElement(pos_ic, "x").text = f"{x:.2f}"
        ET.SubElement(pos_ic, "y").text = f"{y:.2f}"
        ET.SubElement(pos_ic, "z").text = "0.0"

        # Mote ID
        id_ic = make_interface_config(mote, "org.contikios.cooja.contikimote.interfaces.ContikiMoteID")
        ET.SubElement(id_ic, "id").text = str(i)

    # ScriptRunner plugin update
    plugin_found = False
    for plugin in root.findall("plugin"):
        # The class name is stored as text inside <plugin>
        if (plugin.text or "").strip() == "org.contikios.cooja.plugins.ScriptRunner":
            plugin_found = True
            cfg = plugin.find("plugin_config")
            if cfg is None:
                cfg = ET.SubElement(plugin, "plugin_config")
            sf = cfg.find("scriptfile")
            if sf is None:
                sf = ET.SubElement(cfg, "scriptfile")
            sf.text = script_path

            active = cfg.find("active")
            if active is None:
                active = ET.SubElement(cfg, "active")
            active.text = "true"
            break

    if not plugin_found:
        raise SystemExit("Template error: ScriptRunner <plugin> not found")

    indent(root)
    out_path.write_text(ET.tostring(root, encoding="unicode"), encoding="utf-8")
    print(f"Wrote {out_path} with {args.motes} motes, seed={args.seed}, size=({args.width}x{args.height}).")

if __name__ == "__main__":
    main()
