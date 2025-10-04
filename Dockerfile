FROM opensuse/leap:15.6

RUN zypper -n --gpg-auto-import-keys ref && \
    zypper -n --gpg-auto-import-keys up && \
    zypper -n --gpg-auto-import-keys in sysvinit-tools openssh sshfs smbnetfs which bc tar gzip bzip2 curl jq procmail mutt cyrus-sasl-plain postfix postgresql

COPY /VERSION /VERSION
COPY /dump_* /install_* /start /usr/local/bin/
RUN chmod u+x /usr/local/bin/dump_* /usr/local/bin/install_* /usr/local/bin/start

RUN /usr/local/bin/install_azcopy

CMD [ "/usr/local/bin/start" ]
