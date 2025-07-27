FROM ubuntu:22.04

# Install system packages, Java, Python, X11, and tools
RUN apt-get update && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y \
      openjdk-21-jdk git build-essential python3 python3-pip \
      python3-venv python3-setuptools python3-wheel \
      python3-numpy python3-networkx python3-serial \
      doxygen wireshark gradle wget curl xvfb x11-apps unzip rlwrap \
      srecord nano libxrender1 libxtst6 libxi6 libxrandr2 libxinerama1 libfreetype6 libx11-dev libgtk-3-0 libcanberra-gtk* \
    && apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# ---------------------
# Install MSP430 toolchain
# ---------------------
COPY msp430-gcc-9.3.1.11_linux64.tar.bz2 /opt/
RUN cd /opt && \
    tar -xjf msp430-gcc-9.3.1.11_linux64.tar.bz2 && \
    ln -sf /opt/msp430-gcc-9.3.1.11_linux64/bin/msp430-elf-gcc /usr/local/bin/msp430-gcc

# Add MSP430 device headers and linker scripts
COPY include /opt/msp430-gcc-9.3.1.11_linux64/msp430-elf/include
COPY ldscripts /opt/msp430-gcc-9.3.1.11_linux64/msp430-elf/lib/ldscripts

# ---------------------
# Environment setup
# ---------------------
ENV PATH="/opt/msp430-gcc-9.3.1.11_linux64/bin:${PATH}"
ENV JAVA_HOME="/usr/lib/jvm/java-21-openjdk-amd64"
ENV GRADLE_USER_HOME="/workspace/.gradle"

# ---------------------
# Final container setup
# ---------------------
WORKDIR /workspace
CMD ["/bin/bash"]
