#!/usr/bin/env python3
from pathlib import Path

FEATURES = ["etx", "rssi", "pfi", "re", "bdi", "qo", "qlr", "hc", "si", "tv", "pc", "wr", "str"]


def mask_text(feature: str) -> str:
    lines = []
    lines += ["run:", f"  id: solo_{feature}", f'  notes: "SOLO feature mask: only {feature} enabled"', ""]
    lines += ["features:", "  all: false", ""]

    # LINK
    lines += ["  # LINK"]
    lines += [f"  etx:  {'true' if feature == 'etx' else 'false'}"]
    lines += [f"  rssi: {'true' if feature == 'rssi' else 'false'}"]
    lines += [f"  pfi:  {'true' if feature == 'pfi' else 'false'}", ""]

    # NODE
    lines += ["  # NODE"]
    for key in ["re", "bdi", "qo", "qlr", "hc", "si", "tv", "pc"]:
        lines += [f"  {key}:  {'true' if feature == key else 'false'}"]
    lines += [""]

    # NETWORK
    lines += ["  # NETWORK"]
    lines += [f"  wr:   {'true' if feature == 'wr' else 'false'}"]
    lines += [f"  str:  {'true' if feature == 'str' else 'false'}", ""]
    return "\n".join(lines)


def main() -> None:
    project_root = Path(__file__).resolve().parents[1]
    out_dir = project_root / "masks"
    out_dir.mkdir(parents=True, exist_ok=True)

    for feature in FEATURES:
        out_file = out_dir / f"solo_{feature}.yaml"
        out_file.write_text(mask_text(feature), encoding="utf-8")

    print(f"Wrote SOLO masks to: {out_dir}")
    print("Files:", ", ".join([f"solo_{f}.yaml" for f in FEATURES]))


if __name__ == "__main__":
    main()
