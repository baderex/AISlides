from common import BACKEND, PYTHON, run

if __name__ == "__main__":
    run([PYTHON, "-m", "app.cli", "seed"], BACKEND)
