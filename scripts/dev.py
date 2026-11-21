import argparse
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from common import BACKEND, FRONTEND, NG, NODE, PYTHON, WINDOWS, run


def log_output(label, pipe):
    for line in pipe:
        print(f"[{label}] {line.rstrip()}", flush=True)


def stop_all(children):
    for process in reversed(children):
        if process.poll() is not None:
            continue
        try:
            if WINDOWS:
                process.send_signal(signal.CTRL_BREAK_EVENT)
            else:
                os.killpg(process.pid, signal.SIGTERM)
        except OSError:
            pass
    deadline = time.monotonic() + 5
    for process in children:
        try:
            process.wait(timeout=max(0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            if WINDOWS:
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
            else:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass


def launch(test=False):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if not PYTHON.exists() or not NG.exists() or not NODE:
        raise SystemExit("Run python scripts/setup.py before starting the studio.")
    children = []
    env = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"}
    temporary = tempfile.TemporaryDirectory(prefix="framecraft-e2e-") if test else None
    try:
        if temporary:
            root = Path(temporary.name).as_posix()
            env.update(APP_ENV="test", DATABASE_URL=f"sqlite+aiosqlite:///{root}/studio.db", STORAGE_BACKEND="local",
                       LOCAL_STORAGE_PATH=f"{root}/uploads", JOB_BACKEND="local", REDIS_ENABLED="false", LOCAL_SESSION="true")
            run([PYTHON, "-m", "alembic", "upgrade", "head"], BACKEND, env)
            run([PYTHON, "-m", "app.cli", "seed"], BACKEND, env)
        commands = [
            ("api", [PYTHON, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000"], BACKEND),
            ("worker", [PYTHON, "-m", "app.worker"], BACKEND),
            ("web", [NODE, NG, "serve", "--host", "localhost"], FRONTEND),
        ]
        print("Studio  http://localhost:4200\nAPI docs  http://localhost:8000/docs", flush=True)
        for label, command, cwd in commands:
            process = subprocess.Popen(command, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                       text=True, encoding="utf-8", errors="replace",
                                       creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if WINDOWS else 0,
                                       start_new_session=not WINDOWS)
            children.append(process)
            threading.Thread(target=log_output, args=(label, process.stdout), daemon=True).start()
        while True:
            for (label, _, _), process in zip(commands, children):
                if process.poll() is not None:
                    raise SystemExit(f"{label} exited with status {process.returncode}.")
            time.sleep(.25)
    except KeyboardInterrupt:
        pass
    finally:
        stop_all(children)
        if temporary:
            temporary.cleanup()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--test", action="store_true", help="Use an isolated database and the explicit test provider.")
    launch(parser.parse_args().test)
