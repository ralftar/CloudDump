FROM debian:12

RUN apt-get update && \
    apt-get install -y \
    ca-certificates \
    openssh-client \
    sshfs \
    smbnetfs \
    bc \
    tar \
    gzip \
    bzip2 \
    curl \
    jq \
    procmail \
    mutt \
    libsasl2-modules \
    postfix \
    postgresql-client \
    && rm -rf /var/lib/apt/lists/*

COPY /VERSION /VERSION
COPY /dump_*.sh /install_*.sh /start.sh /usr/local/bin/
RUN chmod u+x /usr/local/bin/dump_*.sh /usr/local/bin/install_*.sh /usr/local/bin/start.sh

RUN /usr/local/bin/install_azcopy.sh
RUN /usr/local/bin/install_awscli.sh

CMD [ "/usr/local/bin/start.sh" ]
