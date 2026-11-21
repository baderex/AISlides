import json
import sys
from urllib.error import HTTPError, URLError
from urllib.request import urlopen


def check():
    healthy = True
    try:
        with urlopen("http://localhost:8000/health/ready", timeout=8) as response:
            status = json.load(response)
    except HTTPError as error:
        status = json.load(error)
    except (URLError, TimeoutError):
        print("Backend   unavailable")
        status = {}
    else:
        print("Backend   reachable")
    for service in ("database", "storage", "worker"):
        state = status.get(service, "unavailable")
        print(f"{service.capitalize():<10}{state}")
        healthy &= state == "ok"
    provider = status.get("ai_provider")
    if provider:
        print("AI provider " + ("configured" if provider == "configured" else f"configuration warning ({provider})"))
    try:
        with urlopen("http://localhost:4200", timeout=8) as response:
            frontend = response.status == 200
    except (URLError, TimeoutError):
        frontend = False
    print(f"Frontend  {'ok' if frontend else 'unavailable'}")
    return healthy and frontend


if __name__ == "__main__":
    sys.exit(0 if check() else 1)
