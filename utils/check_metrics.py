#!/usr/bin/env python3
import re, sys, collections

if len(sys.argv) < 2:
    print("Usage: python3 check_metrics.py <file.testlog>")
    sys.exit(1)

fname = sys.argv[1]
pattern = re.compile(r"METRIC\s+([A-Z_]+)")

counts = collections.Counter()

with open(fname, "r", errors="ignore") as f:
    for line in f:
        m = pattern.search(line)
        if m:
            counts[m.group(1)] += 1

print(f"File: {fname}")
if counts:
    for k,v in counts.items():
        print(f"  {k}: {v} occurrences")
else:
    print("  No METRIC entries found.")

