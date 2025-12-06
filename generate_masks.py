#!/usr/bin/env python3
import yaml, os

# All metrics exactly as your system expects
METRICS = [
    "etx", "rssi", "pfi",
    "re", "bdi", "qo", "qlr", "hc", "si", "tv", "pc",
    "wr", "str"
]

OUT_DIR = "testbed/masks"
os.makedirs(OUT_DIR, exist_ok=True)

for metric in METRICS:
    mask = {
        "run": {
            "id": f"mask-{metric}",
            "notes": f"Only {metric.upper()} enabled",
        },
        "features": {
            "all": False
        }
    }

    # all metrics false
    for m in METRICS:
        mask["features"][m] = (m == metric)

    out_path = os.path.join(OUT_DIR, f"mask-{metric}.yaml")
    with open(out_path, "w") as f:
        yaml.dump(mask, f, sort_keys=False)

    print(f"Created {out_path}")

print("\nAll masks generated successfully.")
