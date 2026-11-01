"""Archive the final Git tree and write a checksum manifest (standard library only)."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import subprocess
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[2]
TAG = "hackathon-final"
SAMPLE = "documents/tester_guide/examples/2026003  已经发货等待收钱(1).zip"


def git(*args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(ROOT), *args], check=True, capture_output=True, text=True,
        encoding="utf-8",
    ).stdout.strip()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ref", default=TAG, help="Git ref to archive; default: hackathon-final")
    parser.add_argument("--output", type=Path, default=ROOT / "dist" / f"CrossBot-{TAG}.zip")
    args = parser.parse_args()
    commit = git("rev-parse", "--verify", f"{args.ref}^{{commit}}")
    tree = git("rev-parse", f"{commit}^{{tree}}")
    # NUL-separated output preserves names with spaces and non-ASCII characters.
    names = subprocess.run(
        ["git", "-C", str(ROOT), "ls-tree", "-rz", "--name-only", commit],
        check=True, capture_output=True,
    ).stdout.decode("utf-8").rstrip("\0").split("\0")
    for name in names:
        path = PurePosixPath(name)
        if (path.name == ".env" or path.suffix.lower() in {".db", ".sqlite", ".sqlite3"}
                or path.parts[0] in {".git", ".venv", "data", ".w3-data", "dist"}
                or (path.suffix.lower() == ".zip" and name != SAMPLE)):
            raise SystemExit(f"Ref contains a file excluded from the submission: {name}")
    required = {"app.py", "README.md", "requirements.txt", "THIRD_PARTY_NOTICES.md",
                "docs/submission/platform-config.json", "docs/submission/streamlit-config.toml",
                "docs/submission/third-party-components.json", SAMPLE}
    missing = required.difference(names)
    if missing:
        raise SystemExit(f"Ref is missing submission files: {sorted(missing)}")
    target = args.output.expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    prefix = f"CrossBot-{TAG}/"
    git("archive", "--format=zip", f"--prefix={prefix}", f"--output={target}", commit)
    with ZipFile(target) as archive:
        if archive.testzip() is not None:
            raise SystemExit("Archive CRC check failed")
        members = {name[len(prefix):] for name in archive.namelist() if not name.endswith("/")}
        if members != set(names):
            raise SystemExit("Archive contents do not match the Git tree")
    manifest = {
        "release_tag": TAG, "source_ref": args.ref, "commit": commit, "git_tree": tree,
        "archive": target.name, "tracked_file_count": len(names),
        "archive_bytes": target.stat().st_size,
        "archive_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
        "sample_sha256": "c5c40b4a1c20482a4b6c9cc57f450d03062c5b7a8c767f477fda6b5efbfac7b8",
    }
    target.with_suffix(".manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
