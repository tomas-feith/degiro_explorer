"""Pick a free TCP port, preferring a given one.

Streamlit aborts if its port is taken, which happens whenever another local
dashboard is already running on 8501. The launch scripts call this first and
pass the result through as --server.port.

    python scripts/freeport.py        # prints 8501, or the next free port
    python scripts/freeport.py 8600   # start looking at 8600 instead

The chosen port goes to stdout (so a script can capture it); anything human-
facing goes to stderr. Stdlib only, so this runs before any deps are installed.
"""

from __future__ import annotations

import socket
import sys

DEFAULT_PORT = 8501
SEARCH_LIMIT = 50
CONNECT_TIMEOUT = 0.25  # local-only probe; a listener answers far inside this


def is_free(port: int) -> bool:
    """True if a server can bind this port on all interfaces, as Streamlit does.

    Two checks, because neither alone is enough on Windows:

    1. Connect. Streamlit's server (uvicorn) binds with SO_REUSEADDR, which on
       Windows lets a second process bind the very same port -- so bind() alone
       happily reports a port that already has a dashboard on it as free, and
       you end up with two servers on one port. SO_EXCLUSIVEADDRUSE does not
       help here: it only protects a port when the *first* binder sets it. A
       successful connection is direct proof that something is listening.
    2. Bind. Catches ports that are reserved or bound but not yet listening,
       which a connect probe cannot see.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(CONNECT_TIMEOUT)
        if probe.connect_ex(("127.0.0.1", port)) == 0:
            return False

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind(("", port))
        except OSError:
            return False
    return True


def find_free_port(preferred: int = DEFAULT_PORT, limit: int = SEARCH_LIMIT) -> int:
    for port in range(preferred, preferred + limit):
        if is_free(port):
            return port
    raise RuntimeError(f"no free port in {preferred}..{preferred + limit - 1}")


def main(argv: list[str]) -> int:
    preferred = int(argv[0]) if argv else DEFAULT_PORT
    try:
        port = find_free_port(preferred)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if port != preferred:
        print(f">> port {preferred} is in use, using {port} instead", file=sys.stderr)
    print(port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
