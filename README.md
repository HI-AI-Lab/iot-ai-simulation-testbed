# IoT-AI Simulation Testbed

A Dockerized environment for running the IoT-AI simulation workflow with lab-controlled Contiki-NG and Cooja dependencies.

The standard clone -> setup -> Docker -> build-agent -> run flow is tested on Linux.

## Prerequisites

Before using this repository, make sure Docker is installed on the host machine.

- Install Docker Engine and make sure the Docker service is running.

## Dependency Layout

This repository uses:

- `contiki-ng/` from `https://github.com/HI-AI-Lab/contiki-ng.git`
- `contiki-ng/tools/cooja/` from `https://github.com/HI-AI-Lab/cooja.git`

Do not manually clone upstream Contiki-NG or Cooja into this repo. Use the provided setup script so the expected paths stay correct.

## First-Time Setup

Clone the repository:

```bash
git clone https://github.com/HI-AI-Lab/iot-ai-simulation-testbed.git
cd iot-ai-simulation-testbed
```

Initialize the pinned Contiki-NG and Cooja checkouts:

```bash
./setup_repo.sh
```

The shell scripts can be run directly on Linux.

## Daily Workflow

From the repository root, start Docker with:

```bash
./docker_run.sh
```

This script:

- mounts the repo at `/workspace`
- checks that `contiki-ng` is initialized
- checks that `contiki-ng/tools/cooja` is initialized
- verifies the remotes are the lab forks
- builds the Docker image automatically if it does not already exist
- attaches to the running container if it is already up

Inside the container, rebuild the RL agent when needed:

```bash
./build-agent.sh
```

Run `build-agent.sh` after a fresh clone or after changing Java agent code.

## Run One Simulation

Inside the container, a single simulation run can be launched with:

```bash
python3 run.py \
  --ararl-dir /workspace/experiments/ararl \
  --logs-dir /workspace/results/data/manual_single/logs \
  --gradle-root /workspace/contiki-ng/tools/cooja \
  --nodes 60 \
  --ppm 80 \
  --topology-ids 01 \
  --mask-file /workspace/masks/baseline/etx.yaml \
  --mask-name etx \
  --traffic-seeds 1 \
  --jobs 1 \
  --work-root /workspace/_work
```

This command runs exactly one task because it selects:

- one node count
- one PPM value
- one topology
- one traffic seed
- one mask

## Run A Small GA Test

Inside the container, a small GA test can be launched with:

```bash
python3 run_ga.py \
  --mask-file /workspace/masks/baseline/etx_re_qlr_hc.yaml \
  --ararl-dir /workspace/experiments/ararl \
  --gradle-root /workspace/contiki-ng/tools/cooja \
  --work-root /workspace/_work \
  --ga-out /workspace/results/data/manual_ga \
  --nodes 60 \
  --ppm 80 \
  --topologies 1 \
  --traffic-seeds 1 \
  --population 4 \
  --generations 2 \
  --elite 1 \
  --cx-rate 0.8 \
  --mut-rate 0.08 \
  --jobs 1
```

This keeps the GA run intentionally small for testing:

- one node count
- one PPM value
- one topology
- one traffic seed
- small population
- two generations
