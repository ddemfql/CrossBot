"""Run: python -m storage [--demo]."""
import argparse

from .bootstrap import database_path, import_demo_case, open_store


def main():
    parser = argparse.ArgumentParser(description="Initialize the local CrossBot database")
    parser.add_argument("--demo", action="store_true", help="Import the labeled demo Case")
    args = parser.parse_args()
    store = open_store()
    if args.demo:
        import_demo_case(store)
    print(f"Database: {database_path()}")
    print(f"Cases: {len(store.list_cases())}")


if __name__ == "__main__":
    main()
