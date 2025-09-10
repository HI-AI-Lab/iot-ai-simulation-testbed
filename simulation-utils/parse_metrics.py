import re
import glob
import pandas as pd

files = glob.glob("COOJA-*.testlog")

nlt_pattern = re.compile(r"METRIC NLT DEAD node=(\d+) t_ms=(\d+) energy_mJ=([\d\.]+)")
qlr_pattern = re.compile(r"METRIC QLR node=(\d+) qlr=([\d\.]+) sent=(\d+) dropped=(\d+)")
prr_pattern = re.compile(r"METRIC PRR_LOCAL node=(\d+) prr=([\d\.]+)")
prr_g_pattern = re.compile(r"METRIC PRR_GLOBAL prr=([\d\.]+) recv=(\d+) expected=(\d+)")
e2e_pattern   = re.compile(r"METRIC E2E avg_ms=([\d\.]+) samples=(\d+)")

rows = []
for fname in files:
    # parse filename COOJA-nodes{N}-ppm{P}.testlog
    parts = fname.split("-")
    nodes = int(parts[1].replace("nodes",""))
    ppm   = int(parts[2].replace("ppm","").split(".")[0])

    with open(fname) as f:
        for line in f:
            if "METRIC NLT DEAD" in line:
                m = nlt_pattern.search(line)
                if m:
                    node, t_ms, energy = m.groups()
                    rows.append({
                        "file": fname, "nodes": nodes, "ppm": ppm,
                        "metric": "NLT", "value": int(t_ms)/1000.0  # seconds
                    })
            elif "METRIC QLR" in line:
                m = qlr_pattern.search(line)
                if m:
                    node, qlr, sent, dropped = m.groups()
                    rows.append({
                        "file": fname, "nodes": nodes, "ppm": ppm,
                        "metric": "QLR", "value": float(qlr)
                    })
            elif "METRIC PRR_LOCAL" in line:
                m = prr_pattern.search(line)
                if m:
                    node, prr = m.groups()
                    rows.append({
                        "file": fname, "nodes": nodes, "ppm": ppm,
                        "metric": "PRR", "value": float(prr)
                    })
            elif "METRIC PRR_GLOBAL" in line:
                m = prr_g_pattern.search(line)
                if m:
                    prr, recv, exp = m.groups()
                    rows.append({
                        "file": fname, "nodes": nodes, "ppm": ppm,
                        "metric": "PRR_GLOBAL", "value": float(prr)
                    })
            elif "METRIC E2E" in line:
                m = e2e_pattern.search(line)
                if m:
                    avg_ms, samples = m.groups()
                    rows.append({
                        "file": fname, "nodes": nodes, "ppm": ppm,
                        "metric": "E2E", "value": float(avg_ms)
                    })

df = pd.DataFrame(rows)
df.to_csv("metrics_parsed.csv", index=False)
print("Saved metrics_parsed.csv with", len(df), "rows")
print(df.groupby(["nodes","ppm","metric"])["value"].mean())

