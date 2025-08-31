FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive \
    JAVA_HOME=/usr/lib/jvm/java-21-openjdk-amd64 \
    GRADLE_USER_HOME=/root/.gradle \
    LANG=C.UTF-8 LC_ALL=C.UTF-8 \
    MPLBACKEND=Agg  # headless matplotlib

# Core tools, Java, FANN, and Python data stack (headless)
RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl git unzip \
      build-essential pkg-config \
      openjdk-21-jdk-headless \
      libfann-dev \
      python3 python3-pip \
      python3-numpy python3-pandas python3-scipy python3-networkx python3-serial \
    && rm -rf /var/lib/apt/lists/*

# Minimal plotting (headless)
RUN python3 -m pip install --no-cache-dir --upgrade pip && \
    python3 -m pip install --no-cache-dir matplotlib

# (Optional) MSP430 toolchain — keep if you need to compile MSP430 binaries inside the container
# Provide these files alongside the Dockerfile before building.
COPY msp430-gcc-9.3.1.11_linux64.tar.bz2 /opt/
RUN cd /opt && \
    tar -xjf msp430-gcc-9.3.1.11_linux64.tar.bz2 && \
    ln -sf /opt/msp430-gcc-9.3.1.11_linux64/bin/msp430-elf-gcc /usr/local/bin/msp430-gcc
COPY include   /opt/msp430-gcc-9.3.1.11_linux64/msp430-elf/include
COPY ldscripts /opt/msp430-gcc-9.3.1.11_linux64/msp430-elf/lib/ldscripts

WORKDIR /workspace
CMD ["/bin/bash"]
