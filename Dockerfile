FROM debian:13.7-slim

# Bumped by the check-azcopy workflow (PR on each new azcopy release).
ARG AZCOPY_VERSION=10.34.2
# Kopia for the kopia job type. The checksum is the release's own, from its
# checksums.txt; a new version changes both lines.
ARG KOPIA_VERSION=0.23.1
ARG KOPIA_SHA256=416d0f84a3dbb321a8b2d8f0997b1a0a6e915babe79ee76fa6e4d2bd1e1c5178

RUN apt-get update && \
    apt-get upgrade -y && \
    apt-get install -y --no-install-recommends \
    ca-certificates \
    openssh-client \
    tar \
    gzip \
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
    postgresql-client-19 \
    rsync \
    isync \
    procps \
    && rm -rf /var/lib/apt/lists/* \
    && curl -fsSL -o /tmp/azcopy.tar.gz "https://github.com/Azure/azure-storage-azcopy/releases/download/v${AZCOPY_VERSION}/azcopy_linux_amd64_${AZCOPY_VERSION}.tar.gz" \
    && tar -xzf /tmp/azcopy.tar.gz -C /tmp \
    && install -m 0755 "/tmp/azcopy_linux_amd64_${AZCOPY_VERSION}/azcopy" /usr/local/bin/azcopy \
    && rm -rf /tmp/azcopy.tar.gz "/tmp/azcopy_linux_amd64_${AZCOPY_VERSION}"     && curl -fsSL -o /tmp/kopia.tar.gz "https://github.com/kopia/kopia/releases/download/v${KOPIA_VERSION}/kopia-${KOPIA_VERSION}-linux-x64.tar.gz"     && echo "${KOPIA_SHA256}  /tmp/kopia.tar.gz" | sha256sum -c -     && tar -xzf /tmp/kopia.tar.gz -C /tmp     && install -m 0755 "/tmp/kopia-${KOPIA_VERSION}-linux-x64/kopia" /usr/local/bin/kopia     && rm -rf /tmp/kopia.tar.gz "/tmp/kopia-${KOPIA_VERSION}-linux-x64"

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
