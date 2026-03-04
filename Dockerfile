FROM debian:bookworm-20250317-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    ca-certificates \
    openssh-client \
    sshfs \
    smbnetfs \
    tar \
    gzip \
    bzip2 \
    curl \
    python3 \
    postgresql-client-15 \
    && rm -rf /var/lib/apt/lists/*

COPY install_*.sh /tmp/
RUN chmod +x /tmp/install_*.sh \
    && /tmp/install_azcopy.sh \
    && /tmp/install_awscli.sh \
    && rm /tmp/install_*.sh

COPY clouddump/ /app/clouddump/
WORKDIR /app

ENV PYTHONUNBUFFERED=1

HEALTHCHECK --interval=120s --timeout=5s --retries=2 \
  CMD find /tmp/clouddump-heartbeat -mmin -3 | grep -q .

CMD ["python3", "-m", "clouddump"]
