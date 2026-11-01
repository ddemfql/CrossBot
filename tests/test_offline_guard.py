"""Offline checks allow local asyncio plumbing while rejecting network access."""
import socket
import unittest
from unittest.mock import patch

from tests.run_offline import OfflineSocket


class OfflineGuardTests(unittest.TestCase):
    def test_internet_socket_families_are_rejected_before_connection(self):
        for family in (socket.AF_INET, socket.AF_INET6):
            with self.subTest(family=family), self.assertRaises(AssertionError):
                OfflineSocket(family)

    @unittest.skipUnless(hasattr(socket, "AF_UNIX"), "Unix event-loop socketpair test")
    def test_local_socketpair_still_supports_event_loop_wakeup(self):
        with patch("socket.socket", OfflineSocket):
            left, right = socket.socketpair()
            try:
                left.send(b"synthetic")
                self.assertEqual(right.recv(9), b"synthetic")
            finally:
                left.close()
                right.close()


if __name__ == "__main__":
    unittest.main()

