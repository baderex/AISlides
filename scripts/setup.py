import shutil
import sys

from common import BACKEND, FRONTEND, NPM, PYTHON, ROOT, check_runtimes, run


def main():
    check_runtimes()
    if not PYTHON.exists():
        run([sys.executable, "-m", "venv", BACKEND / ".venv"])
    for directory in (ROOT, BACKEND):
        target = directory / ".env"
        if not target.exists():
            shutil.copyfile(directory / ".env.example", target)
    run([PYTHON, "-m", "pip", "install", "-e", ".[dev]"], BACKEND)
    run([PYTHON, "-m", "playwright", "install", "--only-shell", "chromium"], BACKEND)
    run([NPM, "ci"], FRONTEND)
    run([PYTHON, "-m", "alembic", "upgrade", "head"], BACKEND)
    run([PYTHON, "-m", "app.cli", "seed"], BACKEND)
    print("Setup complete. Run python scripts/dev.py.", flush=True)


if __name__ == "__main__":
    main()
