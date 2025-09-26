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
    && rm -rf /var/lib/apt/lists/* \
    \
    # Install Gradle 8.7 (official distribution)
    && curl -sLo /tmp/gradle-8.7-bin.zip https://services.gradle.org/distributions/gradle-8.7-bin.zip \
    && unzip /tmp/gradle-8.7-bin.zip -d /opt/ \
    && ln -s /opt/gradle-8.7/bin/gradle /usr/local/bin/gradle \
    && rm /tmp/gradle-8.7-bin.zip

# MSP430 toolchain (optional; keep if you use it)
COPY msp430-gcc-9.3.1.11_linux64.tar.bz2 /opt/
RUN cd /opt && \
    tar -xjf msp430-gcc-9.3.1.11_linux64.tar.bz2 && \
    ln -sf /opt/msp430-gcc-9.3.1.11_linux64/bin/msp430-elf-gcc /usr/local/bin/msp430-gcc
COPY include   /opt/msp430-gcc-9.3.1.11_linux64/msp430-elf/include
COPY ldscripts /opt/msp430-gcc-9.3.1.11_linux64/msp430-elf/lib/ldscripts

WORKDIR /workspace
CMD ["/bin/bash"]
