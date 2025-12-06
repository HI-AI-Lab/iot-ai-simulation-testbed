#!/bin/bash

MASK_DIR="testbed/masks"

# Explicit topology IDs so padding is correct
TOPO_IDS="01 02 03 04 05 06 07 08 09 10"

for MASK in $MASK_DIR/*.yaml; do
    MASK_NAME=$(basename "$MASK" .yaml)

    echo "====================================================="
    echo " Running mask: $MASK_NAME   (file: $MASK)"
    echo "====================================================="

    python3 testbed/run_parallel.py \
        --ararl-dir /workspace/testbed/experiments/ararl \
        --logs-dir /workspace/testbed/logs \
        --gradle-root /workspace/contiki-ng/tools/cooja \
        --nodes 60 \
        --ppm 80 100 120 \
        --topology-ids $TOPO_IDS \
        --mask-file "$MASK" \
        --mask-name "$MASK_NAME" \
        --jobs 0

    echo "Mask $MASK_NAME completed."
    echo
done

echo "====================================================="
echo " ALL MASKS COMPLETED"
echo " Results in: /workspace/testbed/logs/"
echo "====================================================="
