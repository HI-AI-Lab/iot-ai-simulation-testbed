# IoT-AI Simulation Testbed

A Dockerized environment for running the IoT-AI simulation workflow with lab-controlled Contiki-NG and Cooja dependencies.

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

On Windows, run the shell scripts from Git Bash.

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
  --mask-file /workspace/mask.yaml \
  --mask-name baseline \
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

## Launch Cooja Manually

Inside the container:

```bash
./contiki-ng/tools/cooja/gradlew -p contiki-ng/tools/cooja run
```

## Important Paths Inside Docker

```text
/workspace/agent
/workspace/experiments/ararl
/workspace/mask.yaml
/workspace/contiki-ng
/workspace/contiki-ng/tools/cooja
```

## Troubleshooting

- If `docker_run.sh` says dependencies are missing, run `./setup_repo.sh`.
- If someone changes a submodule remote, run `./setup_repo.sh` again to resync the lab-managed URLs.
- If you changed Java agent code, rerun `./build-agent.sh` before starting simulations.
- If the Cooja GUI does not open in your environment, check host display forwarding separately from this repository.
