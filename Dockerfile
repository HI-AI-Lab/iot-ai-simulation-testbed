# -------- Base (GPU) --------
FROM nvidia/cuda:12.1.1-cudnn8-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive \
    JAVA_HOME=/usr/lib/jvm/java-21-openjdk-amd64 \
    GRADLE_USER_HOME=/root/.gradle \
    PATH="/opt/msp430-gcc-9.3.1.11_linux64/bin:${PATH}"

# -------- Packages --------
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
      openjdk-21-jdk git build-essential \
      python3 python3-pip python3-venv python3-setuptools python3-wheel \
      python3-numpy python3-networkx python3-serial python3-pandas python3-scipy \
      wget curl unzip ca-certificates gradle \
      xvfb x11-apps \
      libxrender1 libxtst6 libxi6 libxrandr2 libxinerama1 libfreetype6 libx11-dev libgtk-3-0 \
      libcanberra-gtk-module libcanberra-gtk3-module \
      libgl1 libglib2.0-0 ffmpeg \
    && update-ca-certificates && \
    rm -rf /var/lib/apt/lists/*

# -------- MSP430 toolchain --------
COPY msp430-gcc-9.3.1.11_linux64.tar.bz2 /opt/
RUN cd /opt && \
    tar -xjf msp430-gcc-9.3.1.11_linux64.tar.bz2 && \
    ln -sf /opt/msp430-gcc-9.3.1.11_linux64/bin/msp430-elf-gcc /usr/local/bin/msp430-gcc
COPY include   /opt/msp430-gcc-9.3.1.11_linux64/msp430-elf/include
COPY ldscripts /opt/msp430-gcc-9.3.1.11_linux64/msp430-elf/lib/ldscripts

# -------- Python RL stack (GPU) --------
# Match CUDA 12.1 wheels
RUN python3 -m pip install --no-cache-dir --upgrade pip && \
    python3 -m pip install --no-cache-dir \
      --index-url https://download.pytorch.org/whl/cu121 \
      torch torchvision torchaudio && \
    python3 -m pip install --no-cache-dir \
      gymnasium==0.29.1 \
      stable-baselines3==2.3.2 \
      matplotlib \
      tqdm \
      psutil

# -------- Workspace --------
WORKDIR /workspace
CMD ["/bin/bash"]

