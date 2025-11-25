#!/bin/bash
# ==============================================================================
# Topology-aware COOJA runner for your current directory structure
# Works with:
#   /workspace/testbed/experiments/ararl/
#     ├─ Makefile-ppm80 / Makefile-ppm100 / Makefile-ppm120
#     ├─ node.c, sink.c, simulation.js
#     └─ topologies/
#         ├─ N60/  : simulation-nodes60-topo01.csc , positions-simulation-nodes60-topo01.h , ...
#         ├─ N80/
#         └─ N100/
# ==============================================================================

set -euo pipefail

# --------- CONFIGURE HERE ---------
# Arrays for params (you can uncomment/extend as needed)
# nodes=(60 80 100)
# ppms=(80 100 120)
nodes=(60)
ppms=(80)
# Topology IDs present in your folders:
#topo_ids=(01 02 03 04 05 06 07 08 09 10)
topo_ids=(01)

# Paths (relative to /workspace)
LOGS_BASE_DIR="testbed/logs"
ARARL_DIR="testbed/experiments/ararl"
GRADLE_ROOT="contiki-ng/tools/cooja"   # where gradlew lives

# ----------------------------------

echo "Starting COOJA multi-simulations (nodes × ppm × topo)..."
echo "ARARL_DIR       : /workspace/${ARARL_DIR}"
echo "LOGS_BASE_DIR   : /workspace/${LOGS_BASE_DIR}"
echo "Gradle root     : /workspace/${GRADLE_ROOT}"
echo "Nodes           : ${nodes[*]}"
echo "PPMs            : ${ppms[*]}"
echo "Topologies      : ${topo_ids[*]}"
echo

# Ensure we run from /workspace so COOJA.testlog lands in a known place
cd /workspace

# Ensure logs base exists
mkdir -p "${LOGS_BASE_DIR}"

runs_total=0
runs_ok=0

for node_count in "${nodes[@]}"; do
  for ppm_value in "${ppms[@]}"; do
    # Prepare Makefile for this PPM once per (N,PPM) block
    makefile_src="/workspace/${ARARL_DIR}/Makefile-ppm${ppm_value}"
    if [[ ! -f "${makefile_src}" ]]; then
      echo "[ERROR] Makefile for ppm ${ppm_value} not found at ${makefile_src}"
      exit 1
    fi

    for topo in "${topo_ids[@]}"; do
      runs_total=$((runs_total+1))
      echo "----------------------------------------------------------------"
      echo "RUN ${runs_total}: N=${node_count}  PPM=${ppm_value}  topo=${topo}"

      # Source files for this topology
      csc_src="/workspace/${ARARL_DIR}/topologies/N${node_count}/simulation-nodes${node_count}-topo${topo}.csc"
      pos_src="/workspace/${ARARL_DIR}/topologies/N${node_count}/positions-simulation-nodes${node_count}-topo${topo}.h"

      if [[ ! -f "${csc_src}" ]]; then
        echo "[WARN] CSC not found: ${csc_src}  → skipping this run"
        continue
      fi
      if [[ ! -f "${pos_src}" ]]; then
        echo "[WARN] Positions header not found: ${pos_src}  → skipping this run"
        continue
      fi

      # Clean previous build artifacts for a fresh build
      rm -rf "/workspace/${ARARL_DIR}/rpl" "/workspace/${ARARL_DIR}/build" || true

      # Drop scenario assets to canonical names expected by your setup
      cp -f "${csc_src}" "/workspace/${ARARL_DIR}/simulation.csc"
      cp -f "${pos_src}" "/workspace/${ARARL_DIR}/positions-simulation.h"
      cp -f "${makefile_src}" "/workspace/${ARARL_DIR}/Makefile"

      # Path to the CSC we just copied
      CSC_PATH="/workspace/${ARARL_DIR}/simulation.csc"

      # Where to store the output log (structured by N/PPM/topo)
      OUT_DIR="${LOGS_BASE_DIR}/N${node_count}_PPM${ppm_value}/topo${topo}"
      mkdir -p "${OUT_DIR}"

      echo "[INFO] Launching COOJA (no GUI) ..."
      "/workspace/${GRADLE_ROOT}/gradlew" -p "/workspace/${GRADLE_ROOT}" run --args="--no-gui ${CSC_PATH}"

      # COOJA writes COOJA.testlog to the current directory (/workspace)
      if [[ -f "COOJA.testlog" ]]; then
        mv -f "COOJA.testlog" "${OUT_DIR}/COOJA.testlog"
        echo "[OK] Moved log → ${OUT_DIR}/COOJA.testlog"
        runs_ok=$((runs_ok+1))
      else
        echo "[WARN] COOJA.testlog was not created for this run."
      fi

      # Cleanup temp scenario files and build outputs
      rm -f "/workspace/${ARARL_DIR}/Makefile" \
            "/workspace/${ARARL_DIR}/simulation.csc" \
            "/workspace/${ARARL_DIR}/positions-simulation.h" || true
      rm -rf "/workspace/${ARARL_DIR}/rpl" "/workspace/${ARARL_DIR}/build" || true
    done
  done
done

echo "----------------------------------------------------------------"
echo "All simulations done."
echo "Runs attempted : ${runs_total}"
echo "Logs captured  : ${runs_ok}"
echo "Logs directory : /workspace/${LOGS_BASE_DIR}"
