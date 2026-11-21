import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"
WINDOWS = os.name == "nt"
PYTHON = BACKEND / ".venv" / ("Scripts/python.exe" if WINDOWS else "bin/python")
NPM = shutil.which("npm.cmd" if WINDOWS else "npm")
NODE = shutil.which("node")
NG = FRONTEND / "node_modules" / "@angular" / "cli" / "bin" / "ng.js"


def run(args, cwd=ROOT, env=None):
    subprocess.run(args, cwd=cwd, env=env, check=True)


def check_runtimes():
    if sys.version_info < (3, 11):
        raise SystemExit("Python 3.11 or newer is needed.")
    if not NODE or not NPM:
        raise SystemExit("Install Node.js and npm, then retry.")
    version = subprocess.check_output([NODE, "--version"], text=True).strip().lstrip("v")
    major, minor, _ = (int(value) for value in version.split(".", 2))
    if not ((major == 20 and minor >= 19) or (major == 22 and minor >= 13) or major == 24):
        raise SystemExit("Angular 21 needs Node 20.19+, 22.13+, or 24.x. Use Node 22 LTS.")
    print(f"Python {sys.version.split()[0]} and Node {version}", flush=True)
