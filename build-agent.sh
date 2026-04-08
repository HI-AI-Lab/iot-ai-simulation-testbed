#!/bin/bash
set -e

# Paths
AGENT_DIR="/workspace/agent"
COOJA_LIB_DIR="/workspace/contiki-ng/tools/cooja/lib"

# Step 1: Build fat jar with Docker-installed Gradle
echo "[INFO] Building RL Agent fat jar..."
cd "$AGENT_DIR"
gradle clean shadowJar

# Step 2: Locate jar
JAR="$AGENT_DIR/build/libs/rl-agent-all.jar"
if [ ! -f "$JAR" ]; then
  echo "[ERROR] Fat jar not found at $JAR"
  exit 1
fi

# Step 3: Copy to COOJA lib (unpacked mode)
echo "[INFO] Copying jar to $COOJA_LIB_DIR..."
cp "$JAR" "$COOJA_LIB_DIR/rl-agent-all.jar"

# Step 4: Confirm
echo "[SUCCESS] RL Agent jar built and placed at:"
ls -lh "$COOJA_LIB_DIR/rl-agent-all.jar"
