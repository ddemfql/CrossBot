"""Run the available baseline without network; real parser integration is pending."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    tests_root = root / "tests"
    print("Offline baseline: synthetic fixtures, preflight, state/storage regression; real PDF/Excel parsing is pending.", flush=True)
    with tempfile.TemporaryDirectory(prefix=".offline-", dir=tests_root) as temporary:
        # Keep test-created databases and their cleanup inside this workspace.
        if not Path(temporary).resolve().is_relative_to(tests_root.resolve()):
            raise RuntimeError("Temporary test directory escaped workspace")
        with patch("tempfile.tempdir", temporary), patch(
            "socket.socket", side_effect=AssertionError("Offline tests must not use network")
        ):
            suite = unittest.defaultTestLoader.discover(str(tests_root), pattern="test_*.py")
            result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
