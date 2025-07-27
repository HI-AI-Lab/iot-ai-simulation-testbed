# IOT-AI Simulation Testbed (Docker + Contiki-NG + COOJA)

A reproducible, Dockerized platform for running Contiki-NG simulations (COOJA-based) with optional reinforcement learning (Python).

---

## ✅ HOW TO RUN A SIMULATION

### 🧰 1. Install Required Host Packages

```bash
sudo apt update
sudo apt install -y docker.io x11-apps xauth git
sudo usermod -aG docker $USER
newgrp docker
xhost +local:root
```

---

### 📥 2. Clone the Required Repositories

#### Clone Contiki-NG (with submodules)

```bash
cd ~/Documents/IOT-AI
git clone --recurse-submodules https://github.com/contiki-ng/contiki-ng.git
```

---

### 🐳 3. Build the Docker Image

```bash
cd ~/Documents/IOT-AI/testbed
docker build -t cooja-rl-env .
```

---

### 🚀 4. Run the Docker Container (with GUI Access)

```bash
cd ~/Documents/IOT-AI/testbed
docker run -it --rm \
  -v "$PWD":/workspace \
  -v "$PWD/../contiki-ng":/workspace/contiki-ng \
  -e DISPLAY=$DISPLAY \
  -v /tmp/.X11-unix:/tmp/.X11-unix \
  cooja-rl-env
```

✅ You will now be inside the container at `/workspace`.

---

### 🧪 5. Launch COOJA GUI (Inside Docker)

```bash
cd /workspace
./contiki-ng/tools/cooja/gradlew -p contiki-ng/tools/cooja run
```

✅ The COOJA GUI window should appear on your host machine.

---

### 🔧 6. Compile and Run a Simulation

#### Compile Firmware

Example (serial-ask from `moots/`):
```bash
cd /workspace/contiki-ng/examples/serial-ask
make clean
make TARGET=cooja
```

#### Load in COOJA

1. File → New Simulation  
2. Add Mote Type → Compile `serial-ask.c`  
3. Add motes → layout topology  
4. Start simulation  

---

### 🧠 7. (Optional) Run Python RL Agent

If your Contiki app communicates over serial socket:

1. In COOJA → Tools → Serial Socket (set TCP port)
2. In another terminal or inside the container:
```bash
python3 RL-agent/listener.py
```

---

## 📁 PROJECT STRUCTURE (ACTUAL)

```
IOT-AI/
├── contiki-ng/                # Contiki-NG full repo (manually cloned)
└── testbed/
    ├── Dockerfile             # Docker environment
    ├── include/               # MSP430 headers
    ├── ldscripts/             # MSP430 linker scripts
    ├── moots/                 # Your Contiki-NG .c apps (e.g., serial-ask)
    ├── RL-agent/              # Python RL bridge
    ├── msp430-gcc-*.tar.bz2   # MSP430 GCC toolchain
```

---

## 🔗 CONTIKI-NG REPO

This setup works with the official Contiki-NG repository:

```
https://github.com/contiki-ng/contiki-ng.git
```

Use `--recurse-submodules` to ensure all dependencies are included.

---

## 🆘 TROUBLESHOOTING

- **COOJA GUI not opening?**
  - Run `xhost +local:root` on host
  - Test with `xeyes` inside the container

- **Mote not printing?**
  - Double-check `.c` file is compiled and linked to the correct target

- **Python not receiving from COOJA?**
  - Check Serial Socket plugin and ensure port numbers match

---

**You're now ready to simulate and build intelligent IoT networks!**
