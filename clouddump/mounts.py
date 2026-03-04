"""Filesystem mount setup."""

import os
import sys
import time
from pathlib import Path

from clouddump import cfg, run_cmd, log


def setup_mounts(settings):
    """Configure SSH (sshfs) and SMB (smbnetfs) mounts from config.

    SSH paths use "user@host:/path" syntax; SMB paths use "//host/share".
    SMB mounts go through a shared smbnetfs root at /tmp/smbnetfs with
    symlinks to the final mountpoints. Exits the process on mount failure.

    Returns a summary string for inclusion in the startup email.
    """
    mounts = cfg(settings, "mount", [])
    summaries = []

    for i, m in enumerate(mounts):
        path = cfg(m, "path").replace("\\", "/")
        mountpoint = cfg(m, "mountpoint")
        username = cfg(m, "username")
        privkey = cfg(m, "privkey")
        password = cfg(m, "password")
        port = cfg(m, "port")

        if not path or not mountpoint:
            log.error("Mount entry missing 'path' or 'mountpoint' (index %d).", i)
            sys.exit(1)

        summaries.append(f"Path: {path}\nMountpoint {mountpoint}")

        if ":" in path:  # SSH
            if privkey:
                ssh_dir = os.path.expanduser("~/.ssh")
                os.makedirs(ssh_dir, exist_ok=True)
                key_path = os.path.join(ssh_dir, "id_rsa")
                Path(key_path).write_text(privkey)
                os.chmod(key_path, 0o600)

            if "@" not in path and username:
                path = f"{username}@{path}"

            log.info("Mounting %s to %s using sshfs.", path, mountpoint)
            os.makedirs(mountpoint, exist_ok=True)

            cmd = ["sshfs", "-v", "-o", "StrictHostKeyChecking=no"]
            if port:
                cmd += ["-p", str(port)]
            cmd += [path, mountpoint]

            if run_cmd(cmd) != 0:
                log.error("Failed to mount %s to %s using sshfs.", path, mountpoint)
                sys.exit(1)
            log.info("Successfully mounted %s to %s.", path, mountpoint)

        elif path.startswith("//"):  # SMB
            stripped = path.lstrip("/")
            parts = stripped.split("/", 1)
            smb_host = parts[0]
            smb_share = parts[1] if len(parts) > 1 else ""

            log.info("Mounting %s to %s using smbnetfs.", path, mountpoint)
            smbnetfs_root = "/tmp/smbnetfs"

            if not os.path.isdir(os.path.join(smbnetfs_root, smb_host)):
                os.makedirs(smbnetfs_root, exist_ok=True)

                if username:
                    os.makedirs("/dev/shm", exist_ok=True)
                    cred_path = "/dev/shm/.smbcredentials"
                    cred = f"{username}\n{password}" if password else f"{username}\n"
                    Path(cred_path).write_text(cred)
                    os.chmod(cred_path, 0o600)

                    conf_path = "/dev/shm/smbnetfs.conf"
                    Path(conf_path).write_text(f"auth {cred_path}\n")

                    rc = run_cmd(["smbnetfs", smbnetfs_root, "-o", f"config={conf_path},allow_other"])
                else:
                    rc = run_cmd(["smbnetfs", smbnetfs_root, "-o", "allow_other"])

                if rc != 0:
                    log.error("Failed to mount smbnetfs at %s for %s.", smbnetfs_root, path)
                    sys.exit(1)
                time.sleep(2)

            src = os.path.join(smbnetfs_root, smb_host, smb_share)
            if os.path.islink(mountpoint):
                os.remove(mountpoint)
            os.symlink(src, mountpoint)
            log.info("Successfully mounted %s to %s.", path, mountpoint)

        else:
            log.error("Invalid path %s for mountpoint %s.", path, mountpoint)
            log.error('Syntax is "user@host:/path" for SSH, or "//host/path" for SMB.')
            sys.exit(1)

    return "\n\n".join(summaries)
