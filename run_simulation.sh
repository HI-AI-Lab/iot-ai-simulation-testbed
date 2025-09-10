#!/bin/bash

# This script automates running the COOJA simulation for different node counts,
# renaming the output files, and moving them to the simulation-utils directory.
# This version is designed to be run from the /workspace directory and includes
# error checking for a more robust workflow.

echo "Starting the COOJA simulations..."

# Define an array of node counts to simulate.
declare -a nodes=("60" "80" "100")

# Define the ppm value as a global variable.
PPM_VALUE="80"

# Check if the destination directory exists, and create it if not.
DEST_DIR="testbed/simulation-utils"
if [ ! -d "${DEST_DIR}" ]; then
    echo "Destination directory ${DEST_DIR} does not exist. Creating it."
    mkdir -p "${DEST_DIR}"
fi

# Loop through each node count in the array.
for node_count in "${nodes[@]}"
do
    echo "--------------------------------------------------------"
    echo "Running simulation with ${node_count} nodes..."

    # Construct the full path to the .csc file for the current simulation.
    CSC_PATH="/workspace/testbed/experiments/of0/rpl/simulation_gen_${node_count}.csc"

    # Construct the desired output log file name using the variable.
    LOG_FILE="COOJA-ppm${PPM_VALUE}-nodes${node_count}.testlog"

    # Run the simulation. The output will be named 'COOJA.testlog' by default.
    contiki-ng/tools/cooja/gradlew -p contiki-ng/tools/cooja run --args="--no-gui ${CSC_PATH}"

    # Check if the simulation log file was created successfully.
    if [ -f "COOJA.testlog" ]; then
        echo "Simulation complete. Renaming and moving log file."
        
        # Move the log file from the current directory to the correct subdirectory and rename it.
        mv COOJA.testlog "${DEST_DIR}/${LOG_FILE}"
		rm COOJA.testlog

        echo "Moved ${LOG_FILE} to ${DEST_DIR}/."
    else
        echo "Error: The COOJA.testlog file was not created. Skipping file move."
    fi
done

echo "--------------------------------------------------------"
echo "All simulations are complete. The log files have been generated and moved."

