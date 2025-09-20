#!/bin/bash

# ==============================================================================
# Script to manage the Cooja-RL Docker container.
# It follows a specific logic:
# 1. If a container named 'cooja-rl' is already running, it attaches to it.
# 2. If the container is not running but the image exists, it starts a new
#    container.
# 3. If neither the container is running nor the image exists, it builds the
#    Docker image and then starts a new container.
# ==============================================================================

IMAGE_NAME="cooja-headless-rl:latest"
CONTAINER_NAME="cooja-rl"
WORKSPACE_DIR="$HOME/Documents/IOT-AI"
SESSION_NAME="IOT-session"

# Check if a tmux session with the specified name already exists.
# The 'tmux has-session' command returns 0 if the session exists, and 1 if it doesn't.
tmux has-session -t "$SESSION_NAME" 2>/dev/null

if [ $? != 0 ]; then
    echo "Session '$SESSION_NAME' not found. Creating a new one..."
    # Start a new session with the given name and execute the commands inside it.
    tmux new-session -s "$SESSION_NAME" "bash -c 'echo \"New session started. You are now inside tmux.\"; exec bash'"
    # You can also use `tmux new-session -s "$SESSION_NAME"` and then run commands.
    
    # If you want to attach to it after creation, you can add this line:
    # tmux attach-session -t "$SESSION_NAME"  
else
    echo "Session '$SESSION_NAME' already exists. Attaching to it..."
    # Attach to the existing session.
    tmux attach-session -t "$SESSION_NAME"
fi

# Check if a container with the specified name is already running
RUNNING_CONTAINER=$(docker ps -q -f name="$CONTAINER_NAME")

if [ ! -z "$RUNNING_CONTAINER" ]; then
    # Case 1: Container is running
    echo "Found a running container '$CONTAINER_NAME'. Attaching to it..."
    docker exec -it "$CONTAINER_NAME" /bin/bash
else
    # Check if the Docker image exists
    IMAGE_EXISTS=$(docker images -q "$IMAGE_NAME")

    if [ ! -z "$IMAGE_EXISTS" ]; then
        # Case 2: Image exists but container is not running
        echo "Docker image '$IMAGE_NAME' exists. Starting a new container..."
        docker run -it --rm --name "$CONTAINER_NAME" \
            -v "$WORKSPACE_DIR:/workspace" \
            -v cooja_gradle_cache:/root/.gradle \
            -w /workspace "$IMAGE_NAME" bash
    else
        # Case 3: Neither image nor container exists
        echo "Docker image '$IMAGE_NAME' not found. Building the image..."
        # Note: The build command expects the Dockerfile to be in the current directory.
        docker build -t "$IMAGE_NAME" .

        # After building, start the container
        if [ $? -eq 0 ]; then
            echo "Image build successful. Starting a new container..."
            docker run -it --rm --name "$CONTAINER_NAME" \
                -v "$WORKSPACE_DIR:/workspace" \
                -v cooja_gradle_cache:/root/.gradle \
                -w /workspace "$IMAGE_NAME" bash
        else
            echo "Docker build failed. Exiting."
            exit 1
        fi
    fi
fi

