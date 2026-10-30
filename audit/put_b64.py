"""Upload via exec_command + base64 (le SFTP du serveur renvoie ENOENT).

Usage: python put_b64.py <fichier_local> <chemin_distant>
"""
import base64
import os
import sys
from pathlib import Path

import paramiko

HOST = os.environ["ARB_HOST"]
KEY = Path.home() / ".ssh" / "arb_ed25519"

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(HOST, username=os.environ.get("ARB_USER", "root"),
          key_filename=str(KEY), timeout=30)

data = base64.b64encode(Path(sys.argv[1]).resolve().read_bytes())
stdin, stdout, stderr = c.exec_command(f"base64 -d > {sys.argv[2]}")
stdin.write(data)
stdin.channel.shutdown_write()
err = stderr.read().decode("utf-8", "replace")
rc = stdout.channel.recv_exit_status()
if rc != 0 or err.strip():
    sys.exit(f"echec upload rc={rc}: {err}")
print(f"envoye: {sys.argv[1]} ({len(data)} o. b64) -> {sys.argv[2]}")
