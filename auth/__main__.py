"""Create the first administrator without storing a plaintext password."""
import argparse
from getpass import getpass

from storage.bootstrap import open_store
from .service import AuthService


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("username")
    args = parser.parse_args()
    password = getpass("Administrator password (12+ characters): ")
    if password != getpass("Confirm password: "):
        raise SystemExit("Passwords do not match")
    user = AuthService(open_store()).bootstrap_admin(args.username, password)
    print(f"Created administrator: {user.username}")


if __name__ == "__main__":
    main()
