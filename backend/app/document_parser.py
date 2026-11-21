"""Run extraction of untrusted Office and PDF documents in a bounded subprocess."""

import json
import sys


def main():
    try:
        import resource
    except ImportError:
        pass
    else:
        resource.setrlimit(resource.RLIMIT_CPU, (8, 9))
        resource.setrlimit(resource.RLIMIT_AS, (384 * 1024 * 1024, 384 * 1024 * 1024))
    from app.storage import inspect_upload

    media_type, _, text = inspect_upload(sys.argv[1], sys.stdin.buffer.read())
    sys.stdout.buffer.write(json.dumps([media_type, text], ensure_ascii=False).encode("utf-8"))


if __name__ == "__main__":
    main()
