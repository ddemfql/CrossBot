"""Run synthetic core and W3 UI tests without Internet socket access."""
from pathlib import Path
import socket
import sys
import tempfile
import unittest
from unittest.mock import patch


class OfflineSocket(socket.socket):
    """Allow local event-loop socket pairs, never IPv4/IPv6 network sockets."""
    def __init__(self, family=socket.AF_INET, type=socket.SOCK_STREAM, proto=0, fileno=None):
        if not hasattr(socket, "AF_UNIX") or family != socket.AF_UNIX:
            raise AssertionError("Offline tests must not create network sockets")
        super().__init__(family, type, proto, fileno=fileno)


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    tests_root = root / "tests"
    print("Offline baseline: synthetic comparison, parser, provenance, state/storage and UI regression; Internet sockets disabled.", flush=True)
    with tempfile.TemporaryDirectory(prefix=".offline-", dir=tests_root) as temporary:
        # Keep test-created databases and their cleanup inside this workspace.
        if not Path(temporary).resolve().is_relative_to(tests_root.resolve()):
            raise RuntimeError("Temporary test directory escaped workspace")
        with patch("tempfile.tempdir", temporary), patch("socket.socket", OfflineSocket):
            suite = unittest.defaultTestLoader.discover(str(tests_root), pattern="test_*.py")
            result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())

