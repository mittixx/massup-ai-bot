"""Container-only HTTP probe. Never uses external hosts, tokens, or proxy env."""
import json
import os
import sys
from urllib.request import ProxyHandler, build_opener


def main():
    try:
        port = int(os.getenv("PORT", "8000"))
        with build_opener(ProxyHandler({})).open(
            f"http://127.0.0.1:{port}/health", timeout=3
        ) as response:
            result = json.load(response)
            if response.status != 200 or result.get("status") != "ok":
                return 1
            print(f"LOCAL_HTTP_OK version={result.get('version')} port={port} status=200")
            return 0
    except Exception as exc:
        print(f"LOCAL_HTTP_FAILED type={type(exc).__name__}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
