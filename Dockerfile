FROM debian:12.13-slim

RUN apt-get update && \
    apt-get upgrade -y && \
    apt-get install -y --no-install-recommends \
    ca-certificates \
    openssh-client \
    tar \
    gzip \
    bzip2 \
    curl \
    git \
    python3 \
    python3-pip \
    postgresql-client-15 \
    default-mysql-client \
    rsync \
    awscli \
    procps \
    && curl -sSL -o /tmp/packages-microsoft-prod.deb https://packages.microsoft.com/config/debian/12/packages-microsoft-prod.deb \
    && dpkg -i /tmp/packages-microsoft-prod.deb \
    && rm /tmp/packages-microsoft-prod.deb \
    && apt-get update \
    && apt-get install -y --no-install-recommends azcopy \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --break-system-packages -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt

RUN groupadd --system clouddump \
    && useradd --system --gid clouddump --create-home clouddump \
    && mkdir -p /backup /config \
    && chown clouddump:clouddump /backup

COPY clouddump/ /app/clouddump/
WORKDIR /app

ENV PYTHONUNBUFFERED=1

USER clouddump

HEALTHCHECK --interval=120s --timeout=5s --retries=3 \
  CMD find /tmp/clouddump-heartbeat -mmin -10 | grep -q .

CMD ["python3", "-m", "clouddump"]
