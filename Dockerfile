FROM opensuse/leap:15.6

RUN zypper -n --gpg-auto-import-keys ref && \
    zypper -n --gpg-auto-import-keys up && \
    zypper -n --gpg-auto-import-keys in sysvinit-tools openssh sshfs smbnetfs which bc tar gzip bzip2 curl jq procmail mutt cyrus-sasl-plain postfix postgresql mariadb-client

COPY /VERSION /VERSION
COPY /dump_*.sh /install_*.sh /start.sh /usr/local/bin/
RUN chmod u+x /usr/local/bin/dump_*.sh /usr/local/bin/install_*.sh /usr/local/bin/start.sh

RUN /usr/local/bin/install_azcopy.sh

CMD [ "/usr/local/bin/start.sh" ]
