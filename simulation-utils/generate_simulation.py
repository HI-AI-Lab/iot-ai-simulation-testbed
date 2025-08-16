#!/usr/bin/env python3
import argparse, random, xml.etree.ElementTree as ET, copy

def place_points(n, w, h, seed=None, min_dist=None, max_tries=5000):
    """Generate random (x,y) points with optional minimum spacing."""
    if seed is not None:
        random.seed(seed)
    pts = []
    for i in range(n):
        for _ in range(max_tries):
            x = random.uniform(0.0, w)
            y = random.uniform(0.0, h)
            if min_dist is None:
                pts.append((x, y)); break
            ok = True
            for (px, py) in pts:
                dx, dy = x - px, y - py
                if (dx*dx + dy*dy) < (min_dist*min_dist):
                    ok = False; break
            if ok:
                pts.append((x, y)); break
        else:
            # Fallback: fill remaining without spacing if packing fails
            for _ in range(i, n):
                pts.append((random.uniform(0.0, w), random.uniform(0.0, h)))
            break
    return pts

def find_single(root, path, what):
    node = root.find(path)
    if node is None:
        raise ValueError(f"Template missing {what}: xpath '{path}'")
    return node

def ensure_pos_block(interface_config):
    pos = interface_config.find("pos")
    if pos is None:
        pos = ET.Element("pos")
        interface_config.append(pos)
    return pos

def generate(input_csc, output_csc, num_motes, area_w, area_h, seed=None, min_dist=None):
    tree = ET.parse(input_csc)
    root = tree.getroot()

    motetype = find_single(root, ".//motetype", "<motetype>")
    template_mote = find_single(motetype, "mote", "<mote> (template)")

    # Remove all existing motes
    for m in list(motetype.findall("mote")):
        motetype.remove(m)

    coords = place_points(num_motes, area_w, area_h, seed=seed, min_dist=min_dist)

    for i in range(1, num_motes + 1):
        nm = copy.deepcopy(template_mote)

        for ifcfg in nm.findall("interface_config"):
            txt = (ifcfg.text or "").strip()
            if txt == "org.contikios.cooja.contikimote.interfaces.ContikiMoteID":
                id_elem = ifcfg.find("id")
                if id_elem is None:
                    id_elem = ET.Element("id")
                    ifcfg.append(id_elem)
                id_elem.text = str(i)
            elif txt == "org.contikios.cooja.interfaces.Position":
                pos = ensure_pos_block(ifcfg)
                x, y = coords[i-1]
                pos.set("x", f"{x:.6f}")
                pos.set("y", f"{y:.6f}")

        motetype.append(nm)

    try:
        ET.indent(tree, space="  ", level=0)  # Python 3.9+
    except Exception:
        pass

    tree.write(output_csc, encoding="utf-8", xml_declaration=True)

def main():
    ap = argparse.ArgumentParser(description="Generate a COOJA .csc with N motes at random positions")
    ap.add_argument("input", help="Template .csc (must contain one <mote> to clone)")
    ap.add_argument("output", help="Output .csc file")
    ap.add_argument("-n", "--num-motes", type=int, default=10, help="Number of motes (default: 10)")
    ap.add_argument("--area-width", type=float, default=300.0, help="Deployment area width in meters (default: 300)")
    ap.add_argument("--area-height", type=float, default=300.0, help="Deployment area height in meters (default: 300)")
    ap.add_argument("--seed", type=int, default=None, help="Random seed for reproducibility (default: None)")
    ap.add_argument("--min-dist", type=float, default=None, help="Minimum spacing between motes in meters (default: None)")
    args = ap.parse_args()

    generate(args.input, args.output, args.num_motes, args.area_width, args.area_height,
             seed=args.seed, min_dist=args.min_dist)

if __name__ == "__main__":
    main()
