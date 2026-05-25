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
    gnupg \
    && curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc \
       | gpg --dearmor -o /usr/share/keyrings/pgdg.gpg \
    && . /etc/os-release \
    && echo "deb [signed-by=/usr/share/keyrings/pgdg.gpg] http://apt.postgresql.org/pub/repos/apt ${VERSION_CODENAME}-pgdg main" \
       > /etc/apt/sources.list.d/pgdg.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
    postgresql-client-18 \
    default-mysql-client \
    rsync \
    awscli \
    procps \
    && rm -rf /var/lib/apt/lists/* \
    && curl -sSL -o /tmp/azcopy.tar.gz https://aka.ms/downloadazcopy-v10-linux \
    && tar -xzf /tmp/azcopy.tar.gz -C /tmp \
    && install -m 0755 /tmp/azcopy_linux_amd64_*/azcopy /usr/local/bin/azcopy \
    && rm -rf /tmp/azcopy.tar.gz /tmp/azcopy_linux_amd64_*

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

EXPOSE 8080

USER clouddump


CMD ["python3", "-m", "clouddump"]
