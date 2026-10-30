"""Execute une commande sur le serveur de campagne.

Usage: python ssh_run.py "<commande>" [timeout_s]
       python ssh_run.py --put <fichier_local> <chemin_distant>
Hote: variable ARB_HOST. Authentification: cle ~/.ssh/arb_ed25519,
sinon mot de passe dans la variable ARB_PASS (jamais dans ce fichier).
"""
import os
import sys
from pathlib import Path

import paramiko

HOST = os.environ.get("ARB_HOST")
USER = os.environ.get("ARB_USER", "root")
KEY = Path.home() / ".ssh" / "arb_ed25519"
if not HOST:
    sys.exit("definir ARB_HOST (IP du serveur)")

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
if KEY.exists() and not os.environ.get("ARB_PASS"):
    c.connect(HOST, username=USER, key_filename=str(KEY), timeout=30)
else:
    c.connect(HOST, username=USER, password=os.environ["ARB_PASS"], timeout=30)

if sys.argv[1] == "--put":
    sftp = c.open_sftp()
    sftp.put(sys.argv[2], sys.argv[3])
    sftp.close()
    print(f"envoye: {sys.argv[2]} -> {sys.argv[3]}")
    sys.exit(0)

cmd = sys.argv[1]
timeout = int(sys.argv[2]) if len(sys.argv) > 2 else 120
stdin, stdout, stderr = c.exec_command(cmd, timeout=timeout)
out = stdout.read().decode("utf-8", "replace")
err = stderr.read().decode("utf-8", "replace")
rc = stdout.channel.recv_exit_status()
sys.stdout.buffer.write(out.encode("utf-8", "replace"))
sys.stdout.buffer.flush()
if err.strip():
    sys.stderr.write(err)
sys.exit(rc)
