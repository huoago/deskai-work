from __future__ import annotations

import argparse
import sys

import uvicorn

from app.core.config import Settings
from app.main import create_app


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="DeskAI local engine")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--session-token", default=None)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        print("DeskAI Engine refuses non-loopback hosts.", file=sys.stderr)
        return 2
    app = create_app(Settings.load(session_token=args.session_token))
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
