FROM debian:12.13-slim

RUN apt-get update && \
    apt-get upgrade -y && \
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
    awscli \
    && curl -sSL -o /tmp/packages-microsoft-prod.deb https://packages.microsoft.com/config/debian/12/packages-microsoft-prod.deb \
    && dpkg -i /tmp/packages-microsoft-prod.deb \
    && rm /tmp/packages-microsoft-prod.deb \
    && apt-get update \
    && apt-get install -y --no-install-recommends azcopy \
    && rm -rf /var/lib/apt/lists/*

COPY clouddump/ /app/clouddump/
WORKDIR /app

ENV PYTHONUNBUFFERED=1

HEALTHCHECK --interval=120s --timeout=5s --retries=2 \
  CMD find /tmp/clouddump-heartbeat -mmin -3 | grep -q .

CMD ["python3", "-m", "clouddump"]
