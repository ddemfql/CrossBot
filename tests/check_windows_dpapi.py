"""Deployment gate: no fake cipher, no skip, no secret output."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from providers.user_settings import WindowsDPAPI
from core.errors import CrossBotError


def main():
    cipher = WindowsDPAPI()
    synthetic = b"CrossBot synthetic DPAPI deployment check"
    entropy = b"CrossBot synthetic application user"
    try:
        encrypted = cipher.encrypt(synthetic, entropy)
        if cipher.decrypt(encrypted, entropy) != synthetic:
            print("DPAPI current-user synthetic roundtrip: FAIL")
            return 1
    except CrossBotError as error:
        print(error.message)
        return 1
    print("DPAPI current-user synthetic roundtrip: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
