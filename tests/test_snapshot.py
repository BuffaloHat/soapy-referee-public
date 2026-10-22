import pytest

from evals.db import DEFAULT_DB_PATH, connect
from evals.snapshot import SnapshotError, check_file, check_tables, load_manifest


def test_file_matches_manifest():
    check_file(DEFAULT_DB_PATH)


def test_tables_match_manifest():
    con = connect(verify=False)
    try:
        check_tables(con)
    finally:
        con.close()


def test_missing_file_fails():
    with pytest.raises(SnapshotError, match="not found"):
        check_file(DEFAULT_DB_PATH.with_name("nope.sqlite"))


def test_wrong_checksum_fails():
    manifest = load_manifest() | {"sha256": "0" * 64}
    with pytest.raises(SnapshotError, match="checksum changed"):
        check_file(DEFAULT_DB_PATH, manifest)


def test_wrong_size_fails():
    manifest = load_manifest() | {"bytes": 1}
    with pytest.raises(SnapshotError, match="size changed"):
        check_file(DEFAULT_DB_PATH, manifest)


def test_wrong_row_count_fails():
    manifest = load_manifest()
    manifest["tables"] = {"game": 1}
    con = connect(verify=False)
    try:
        with pytest.raises(SnapshotError, match="drift"):
            check_tables(con, manifest)
    finally:
        con.close()
