# ---- GPU + Cooja + RL (Ubuntu 22.04) ----
FROM nvidia/cuda:12.1.1-cudnn8-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive

# Base tools (keep close to your original)
RUN apt-get update && apt-get install -y \
    openjdk-21-jdk git build-essential \
    python3 python3-pip python3-venv python3-setuptools python3-wheel \
    python3-numpy python3-networkx python3-serial \
    doxygen wireshark gradle wget curl xvfb x11-apps unzip rlwrap srecord nano \
    libxrender1 libxtst6 libxi6 libxrandr2 libxinerama1 libfreetype6 libx11-dev libgtk-3-0 libcanberra-gtk* \
    ca-certificates pkg-config && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# ---------------------
# Install MSP430 toolchain
# ---------------------
# Assumes the tarball + your custom 'include' and 'ldscripts' folders
# are present in the Docker build context next to this Dockerfile.
COPY msp430-gcc-9.3.1.11_linux64.tar.bz2 /opt/
RUN cd /opt && \
    tar -xjf msp430-gcc-9.3.1.11_linux64.tar.bz2 && \
    ln -sf /opt/msp430-gcc-9.3.1.11_linux64/bin/msp430-elf-gcc /usr/local/bin/msp430-gcc

# Add MSP430 device headers and linker scripts (if you maintain custom ones)
COPY include   /opt/msp430-gcc-9.3.1.11_linux64/msp430-elf/include
COPY ldscripts /opt/msp430-gcc-9.3.1.11_linux64/msp430-elf/lib/ldscripts

# Quick sanity checks (won't fail build if not present)
RUN test -x /opt/msp430-gcc-9.3.1.11_linux64/bin/msp430-elf-gcc || (echo "msp430-elf-gcc missing!" && false) && \
    test -d /opt/msp430-gcc-9.3.1.11_linux64/msp430-elf/include || echo "NOTE: MSP430 include dir not found (COPY include skipped?)" && \
    test -d /opt/msp430-gcc-9.3.1.11_linux64/msp430-elf/lib/ldscripts || echo "NOTE: MSP430 ldscripts dir not found (COPY ldscripts skipped?)"

# ---------------------
# Python ML stack (PyTorch GPU)
# ---------------------
RUN python3 -m pip install --no-cache-dir -U pip setuptools wheel && \
    python3 -m pip install --no-cache-dir \
      torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu121 && \
    python3 -m pip install --no-cache-dir pandas matplotlib psutil regex tqdm

# ---------------------
# Environment setup
# ---------------------
ENV PATH="/opt/msp430-gcc-9.3.1.11_linux64/bin:${PATH}"
ENV JAVA_HOME="/usr/lib/jvm/java-21-openjdk-amd64"
ENV GRADLE_USER_HOME="/workspace/.gradle"

# If you ever hit Gradle/JDK compatibility issues, switch to JDK 17 instead:
#   apt-get install -y openjdk-17-jdk
#   ENV JAVA_HOME="/usr/lib/jvm/java-17-openjdk-amd64"

# ---------------------
# Final container setup
# ---------------------
WORKDIR /workspace
CMD ["/bin/bash"]
