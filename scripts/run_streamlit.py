"""Launch the local Streamlit cockpit.

Wraps `streamlit run src/app/streamlit_main.py` so the user has one
predictable entry point and so we can pass STREAMLIT_* env vars from `.env`.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    app_path = repo_root / "src" / "app" / "streamlit_main.py"

    if not app_path.exists():
        print(f"ERROR: cockpit entry not found at {app_path}", file=sys.stderr)
        return 1

    port = os.environ.get("STREAMLIT_PORT", "8501")
    address = os.environ.get("STREAMLIT_SERVER_ADDRESS", "127.0.0.1")

    cmd = [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        str(app_path),
        "--server.port",
        port,
        "--server.address",
        address,
        "--server.headless",
        "true",
    ]
    print(f"$ {' '.join(cmd)}")
    return subprocess.call(cmd)


if __name__ == "__main__":
    raise SystemExit(main())
