FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV JAVA_HOME=/usr/lib/jvm/java-21-openjdk-amd64
ENV GRADLE_USER_HOME=/root/.gradle
ENV LANG=C.UTF-8
ENV LC_ALL=C.UTF-8
ENV MPLBACKEND=Agg
ENV PATH=/opt/msp430-gcc-9.3.1.11_linux64/bin:${PATH}

# Core tools, Java, FANN, Python (headless data stack)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl git unzip \
    build-essential pkg-config \
    openjdk-21-jdk-headless \
    libfann-dev \
    python3 python3-pip python3-venv python3-setuptools python3-wheel \
    python3-numpy python3-pandas python3-scipy python3-networkx python3-serial \
    python3-matplotlib \
    && rm -rf /var/lib/apt/lists/*

# --- Py4J (Python package for your agent) ---
RUN pip3 install --no-cache-dir py4j==0.10.9.7

# MSP430 toolchain (optional; keep if you use it)
COPY msp430-gcc-9.3.1.11_linux64.tar.bz2 /opt/
RUN cd /opt && \
    tar -xjf msp430-gcc-9.3.1.11_linux64.tar.bz2 && \
    ln -sf /opt/msp430-gcc-9.3.1.11_linux64/bin/msp430-elf-gcc /usr/local/bin/msp430-gcc
COPY include   /opt/msp430-gcc-9.3.1.11_linux64/msp430-elf/include
COPY ldscripts /opt/msp430-gcc-9.3.1.11_linux64/msp430-elf/lib/ldscripts

# --- Py4J JAR for COOJA (Java side only; bridge handled separately) ---
ARG PY4J_VERSION=0.10.9.7
RUN mkdir -p /opt/py4j \
 && curl -fsSL -o /opt/py4j/py4j-${PY4J_VERSION}.jar \
      https://repo1.maven.org/maven2/org/py4j/py4j/${PY4J_VERSION}/py4j-${PY4J_VERSION}.jar

# Helper that installs ONLY the Py4J JAR into COOJA when the folder exists
RUN printf '%s\n' \
'#!/usr/bin/env bash' \
'set -euo pipefail' \
'LIB="/workspace/contiki-ng/tools/cooja/dist/lib"' \
'JAR="/opt/py4j/py4j-'"${PY4J_VERSION}"'.jar"' \
'[ -d "$LIB" ] || exit 0' \
'mkdir -p "$LIB"' \
'install -m 0644 -T "$JAR" "$LIB/$(basename "$JAR")"' \
'echo "[py4j] installed $(basename "$JAR") -> $LIB" >&2' \
> /usr/local/bin/install-py4j-into-cooja && chmod +x /usr/local/bin/install-py4j-into-cooja

# Auto-run the installer on every interactive shell (covers bind-mounted contiki-ng)
RUN printf '\n%s\n' \
'if [ -x /usr/local/bin/install-py4j-into-cooja ]; then /usr/local/bin/install-py4j-into-cooja || true; fi' \
>> /etc/bash.bashrc


WORKDIR /workspace
CMD ["/bin/bash"]
