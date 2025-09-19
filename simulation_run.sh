#!/bin/bash

# ==============================================================================
# This script automates running COOJA simulations for a range of node counts
# and PPM (parts per million) values, organizing the output log files.
# It is designed to be run from the /workspace directory within the Docker container.
# ==============================================================================

echo "Starting the COOJA multi-simulations..."

# Define arrays for the different simulation parameters.
declare -a nodes=("60" "80" "100")
declare -a ppm_values=("80" "100" "120")

# Define the base directory for all simulation logs.
LOGS_BASE_DIR="testbed/simulation-logs"
MRHOF_DIR="testbed/experiments/mrhof"

# Check if the base log directory exists, and create it if it doesn't.
if [ ! -d "${LOGS_BASE_DIR}" ]; then
    echo "Base logs directory ${LOGS_BASE_DIR} does not exist. Creating it."
    mkdir -p "${LOGS_BASE_DIR}"
fi

# Loop through each node count.
for node_count in "${nodes[@]}"
do
    echo "--------------------------------------------------------"
    echo "Running simulation with ${node_count} nodes..."
    
    # --- Clean build directories before starting simulation ---
    # This ensures a fresh compilation and prevents issues from previous runs.
    echo "Cleaning build and rpl directories for fresh compilation..."
    rm -rf "${MRHOF_DIR}/rpl"
    rm -rf "${MRHOF_DIR}/build"
    # -------------------------------------------------------------

    # --- Create a generic simulation file for this node count ---
    # We copy the specific node count file to a generic name.
    echo "Creating a generic simulation.csc file from simulation-nodes${node_count}.csc..."
    cp "/workspace/${MRHOF_DIR}/simulation-nodes${node_count}.csc" "/workspace/${MRHOF_DIR}/simulation.csc"
    # -------------------------------------------------------------

    # Loop through each PPM value.
    for ppm_value in "${ppm_values[@]}"
    do
        echo "Running simulation for ${node_count} nodes and ${ppm_value} ppm..."

        # --- NEW: Copy the appropriate Makefile for the current PPM value ---
        echo "Copying Makefile for ppm ${ppm_value}..."
        cp "/workspace/${MRHOF_DIR}/Makefile-ppm${ppm_value}" "/workspace/${MRHOF_DIR}/Makefile"
        # ------------------------------------------------------------------

        # Construct the full path to the .csc file.
        CSC_PATH="/workspace/${MRHOF_DIR}/simulation.csc"

        # Construct the desired output log file name.
        LOG_FILE_NAME="ppm${ppm_value}_nodes${node_count}.testlog"
        OUTPUT_LOG_PATH="${LOGS_BASE_DIR}/${LOG_FILE_NAME}"

        # Run the simulation. The output will be named 'COOJA.testlog' by default.
        # Ensure you are in the correct directory for the gradlew command to work.
        contiki-ng/tools/cooja/gradlew -p contiki-ng/tools/cooja run --args="--no-gui ${CSC_PATH}"

        # Check if the simulation log file was created successfully.
        if [ -f "COOJA.testlog" ]; then
            echo "Simulation complete. Renaming and moving log file."

            # Move the log file from the current directory to the new logs directory.
            mv COOJA.testlog "${OUTPUT_LOG_PATH}"

            echo "Moved ${LOG_FILE_NAME} to ${LOGS_BASE_DIR}/."
        else
            echo "Error: The COOJA.testlog file was not created. Skipping file move."
        fi
    done
done

echo "--------------------------------------------------------"
echo "All 9 simulations are complete. The log files have been generated and moved to ${LOGS_BASE_DIR}."

# --- Cleanup temporary files ---
echo "Cleaning up temporary files..."
if [ -f "COOJA.testlog" ]; then
    rm "COOJA.testlog"
fi
if [ -f "${MRHOF_DIR}/Makefile" ]; then
    rm "${MRHOF_DIR}/Makefile"
fi
if [ -f "${MRHOF_DIR}/simulation.csc" ]; then
    rm "${MRHOF_DIR}/simulation.csc"
fi
echo "Cleanup complete."
