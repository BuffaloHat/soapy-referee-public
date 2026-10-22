"""The snapshot manifest: what the frozen NBA database must look like.

Gold answers are only valid against the exact file they were written for, so
drift has to fail loudly rather than quietly changing results.
"""

import hashlib
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = ROOT / "evals" / "snapshot.yaml"
READ_CHUNK = 8 << 20


class SnapshotError(Exception):
    """The snapshot on disk is not the one the gold set was built against."""


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(READ_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_manifest() -> dict:
    return yaml.safe_load(MANIFEST_PATH.read_text())


def check_file(path: Path, manifest: dict | None = None) -> None:
    """Fail unless the file matches the manifest. Cheap: no DB connection."""
    manifest = manifest or load_manifest()
    if not path.exists():
        raise SnapshotError(f"snapshot not found: {path}")

    size = path.stat().st_size
    if size != manifest["bytes"]:
        raise SnapshotError(
            f"snapshot size changed: expected {manifest['bytes']:,} bytes, found {size:,}"
        )

    digest = file_sha256(path)
    if digest != manifest["sha256"]:
        raise SnapshotError(
            "snapshot checksum changed — every gold answer is invalid.\n"
            f"  expected {manifest['sha256']}\n"
            f"  found    {digest}"
        )


def check_tables(con, manifest: dict | None = None) -> None:
    """Fail unless every table still has the row count the manifest records."""
    manifest = manifest or load_manifest()
    problems = []
    for table, expected in manifest["tables"].items():
        found = con.execute(f"SELECT count(*) FROM nba.{table}").fetchone()[0]
        if found != expected:
            problems.append(f"{table}: expected {expected:,} rows, found {found:,}")
    if problems:
        raise SnapshotError("snapshot drift:\n  " + "\n  ".join(problems))


def build_manifest(con, path: Path) -> dict:
    tables = {
        row[0]: con.execute(f"SELECT count(*) FROM nba.{row[0]}").fetchone()[0]
        for row in con.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_catalog = 'nba' ORDER BY table_name"
        ).fetchall()
    }
    return {
        "source": "Kaggle wyattowalsh/basketball — nba.sqlite",
        "sha256": file_sha256(path),
        "bytes": path.stat().st_size,
        "tables": tables,
    }
