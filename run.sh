#!/bin/bash
# ==============================================================================
# Topology-aware COOJA runner (prints PRR, QLR, E2E, NLT after each run)
# Directory layout expected:
#   /workspace/testbed/experiments/ararl/
#     ├─ Makefile-ppm80 / Makefile-ppm100 / Makefile-ppm120
#     ├─ node.c, sink.c, simulation.js
#     └─ topologies/N60|N80|N100/simulation-nodes<NN>-topo<TT>.csc, positions-...-topo<TT>.h
# ==============================================================================

set -euo pipefail

# --------- CONFIGURE HERE ---------
# nodes=(60 80 100)
# ppms=(80 100 120)
nodes=(60)
ppms=(80)
# topo_ids=(01 02 03 04 05 06 07 08 09 10)
topo_ids=(01)

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

cd /workspace
mkdir -p "${LOGS_BASE_DIR}"

SUMMARY_CSV="${LOGS_BASE_DIR}/summary.csv"
if [[ ! -f "${SUMMARY_CSV}" ]]; then
  echo "nodes,ppm,topo,prr,qlr,e2e_mean_ms,nlt_first_energy_ms" > "${SUMMARY_CSV}"
fi

runs_total=0
runs_ok=0

for node_count in "${nodes[@]}"; do
  for ppm_value in "${ppms[@]}"; do
    makefile_src="/workspace/${ARARL_DIR}/Makefile-ppm${ppm_value}"
    if [[ ! -f "${makefile_src}" ]]; then
      echo "[ERROR] Makefile for ppm ${ppm_value} not found at ${makefile_src}"
      exit 1
    fi

    for topo in "${topo_ids[@]}"; do
      runs_total=$((runs_total+1))
      echo "----------------------------------------------------------------"
      echo "RUN ${runs_total}: N=${node_count}  PPM=${ppm_value}  topo=${topo}"

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

      rm -rf "/workspace/${ARARL_DIR}/rpl" "/workspace/${ARARL_DIR}/build" || true
      cp -f "${csc_src}" "/workspace/${ARARL_DIR}/simulation.csc"
      cp -f "${pos_src}" "/workspace/${ARARL_DIR}/positions-simulation.h"
      rm -f "/workspace/${ARARL_DIR}/Makefile" || true
      cp -f "${makefile_src}" "/workspace/${ARARL_DIR}/Makefile" 
      
	  echo "[INFO] Rebuilding firmware for PPM=${ppm_value}..."
      make -C "/workspace/${ARARL_DIR}" clean
      make -C "/workspace/${ARARL_DIR}"

      CSC_PATH="/workspace/${ARARL_DIR}/simulation.csc"
      OUT_DIR="${LOGS_BASE_DIR}/N${node_count}_PPM${ppm_value}/topo${topo}"
      mkdir -p "${OUT_DIR}"

      echo "[INFO] Launching COOJA (no GUI) ..."
      "/workspace/${GRADLE_ROOT}/gradlew" -p "/workspace/${GRADLE_ROOT}" run --args="--no-gui ${CSC_PATH}"

      if [[ -f "COOJA.testlog" ]]; then
        mv -f "COOJA.testlog" "${OUT_DIR}/COOJA.testlog"
        echo "[OK] Moved log → ${OUT_DIR}/COOJA.testlog"
        runs_ok=$((runs_ok+1))

        # ---- Print & save metrics (BusyBox-safe AWK) ----
        MET_FILE="${OUT_DIR}/metrics.txt"
        awk '
          BEGIN{first=0; gen=0; ql=0; trecv=0; we=0}
          $0 ~ /WRAPUP node_id=/ && $0 !~ /node_id=1/ {
            s=$0
            if (match(s,/Gen=[0-9]+/))   { t=substr(s,RSTART,RLENGTH); sub(/Gen=/,"",t);   gen+=t+0 }
            if (match(s,/QLoss=[0-9]+/)) { t=substr(s,RSTART,RLENGTH); sub(/QLoss=/,"",t); ql +=t+0 }
            em=0
            if (match(s,/end_ms=[0-9]+/)) { t=substr(s,RSTART,RLENGTH); sub(/end_ms=/,"",t); em=t+0 }
            if (s ~ /reason=energy/ && em>0 && (first==0 || em<first)) first=em
          }
          $0 ~ /SINK_SUMMARY node=/ {
            s=$0; r=0; e=0
            if (match(s,/Recv=[0-9]+/))   { t=substr(s,RSTART,RLENGTH); sub(/Recv=/,"",t);   r=t+0 }
            if (match(s,/AvgE2E=[0-9]+/)) { t=substr(s,RSTART,RLENGTH); sub(/AvgE2E=/,"",t); e=t+0 }
            trecv+=r; we+=r*e
          }
          END{
            prr = (gen?trecv/gen:0)
            qlr = (gen?ql/gen:0)
            e2e = (trecv?we/trecv:0)
            printf("PRR=%.6f\nQLR=%.9f\nE2E_mean_ms=%.2f\nNLT_first_energy_ms=%d\n", prr, qlr, e2e, first)
          }
        ' "${OUT_DIR}/COOJA.testlog" | tee "${MET_FILE}"

        # Append CSV line
        awk -v N="${node_count}" -v P="${ppm_value}" -v T="topo${topo}" '
          BEGIN{first=0; gen=0; ql=0; trecv=0; we=0}
          $0 ~ /WRAPUP node_id=/ && $0 !~ /node_id=1/ {
            s=$0
            if (match(s,/Gen=[0-9]+/))   { t=substr(s,RSTART,RLENGTH); sub(/Gen=/,"",t);   gen+=t+0 }
            if (match(s,/QLoss=[0-9]+/)) { t=substr(s,RSTART,RLENGTH); sub(/QLoss=/,"",t); ql +=t+0 }
            em=0
            if (match(s,/end_ms=[0-9]+/)) { t=substr(s,RSTART,RLENGTH); sub(/end_ms=/,"",t); em=t+0 }
            if (s ~ /reason=energy/ && em>0 && (first==0 || em<first)) first=em
          }
          $0 ~ /SINK_SUMMARY node=/ {
            s=$0; r=0; e=0
            if (match(s,/Recv=[0-9]+/))   { t=substr(s,RSTART,RLENGTH); sub(/Recv=/,"",t);   r=t+0 }
            if (match(s,/AvgE2E=[0-9]+/)) { t=substr(s,RSTART,RLENGTH); sub(/AvgE2E=/,"",t); e=t+0 }
            trecv+=r; we+=r*e
          }
          END{
            prr = (gen?trecv/gen:0)
            qlr = (gen?ql/gen:0)
            e2e = (trecv?we/trecv:0)
            printf("%d,%d,%s,%.6f,%.9f,%.2f,%d\n", N, P, T, prr, qlr, e2e, first)
          }
        ' "${OUT_DIR}/COOJA.testlog" >> "${SUMMARY_CSV}"

      else
        echo "[WARN] COOJA.testlog was not created for this run."
      fi

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
echo "Summary CSV    : /workspace/${SUMMARY_CSV}"
