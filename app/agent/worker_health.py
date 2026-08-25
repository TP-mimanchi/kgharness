"""Container health probe for the Agent worker event loop."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from app.core.config import settings


def main() -> int:
    health_file = Path(
        os.getenv("AGENT_WORKER_HEALTH_FILE", "/tmp/kgharness-agent-worker.ready")
    )
    if not health_file.is_file():
        print("worker heartbeat file is missing", file=sys.stderr)
        return 1
    age = time.time() - health_file.stat().st_mtime
    maximum_age = max(
        settings.worker_registry_ttl_seconds,
        settings.worker_heartbeat_seconds * 3,
    )
    if age > maximum_age:
        print(f"worker heartbeat is stale ({age:.1f}s)", file=sys.stderr)
        return 1
    print(f"worker heartbeat is healthy ({age:.1f}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
