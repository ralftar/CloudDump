FROM debian:12

RUN apt-get update && \
    apt-get install -y \
    ca-certificates \
    openssh-client \
    sshfs \
    smbnetfs \
    tar \
    gzip \
    bzip2 \
    curl \
    python3 \
    postgresql-client \
    && rm -rf /var/lib/apt/lists/*

COPY VERSION /VERSION
COPY install_*.sh /tmp/
RUN chmod +x /tmp/install_*.sh \
    && /tmp/install_azcopy.sh \
    && /tmp/install_awscli.sh \
    && rm /tmp/install_*.sh

COPY start.py /usr/local/bin/start.py
RUN chmod +x /usr/local/bin/start.py

CMD ["python3", "/usr/local/bin/start.py"]
