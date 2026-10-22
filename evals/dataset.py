"""The gold set as a fuse dataset, the anchor for Phase 7 experiments.

An experiment in fuse is a set of traces, one per dataset item, so the gold
cases have to exist there as items first. This copies them across; the YAML in
`evals/cases/` stays the source of truth, and re-running the sync updates the
items in place (each item's id is its case id).

What goes in an item, and what doesn't:

- **input:** the question, the same string the case trace's root carries.
- **metadata:** case id, tier, expects.
- **no expected output.** Gold is executed, not stored (overview.md, principle 3). In fuse
  v4 an experiment trace carries its own expected output, so the gold answer
  is attached at run time from the gold SQL just executed, never copied here
  where it could drift from the snapshot.

Items in fuse with no matching case are reported, not deleted.

    uv run python -m evals.dataset
"""

import argparse
import json
import urllib.parse
from pathlib import Path

from dotenv import load_dotenv

from evals import cases as case_loader
from evals.api import fuse, http

DATASET_NAME = "sref-gold"
DESCRIPTION = ("SREF gold cases: NBA questions for the text-to-SQL agent. "
               "Synced from evals/cases/; expected answers come from gold SQL at run time.")


def item(case) -> dict:
    return {
        "datasetName": DATASET_NAME,
        "id": case.id,
        "input": case.question,
        "metadata": {"case_id": case.id, "tier": case.tier, "expects": case.expects},
    }


def existing_ids(host: str, headers: dict, call=http) -> set[str]:
    ids, page = set(), 1
    while True:
        query = urllib.parse.urlencode({"datasetName": DATASET_NAME, "page": page, "limit": 50})
        status, text = call("GET", f"{host}/api/public/dataset-items?{query}", headers)
        if not 200 <= status < 300:
            raise RuntimeError(f"listing items: {status} {text}")
        body = json.loads(text)
        ids |= {i["id"] for i in body["data"]}
        if page >= body["meta"]["totalPages"]:
            return ids
        page += 1


def sync(suite, call=http) -> dict:
    """Create the dataset if needed and upsert one item per case."""
    host, headers = fuse()
    status, text = call("POST", f"{host}/api/public/v2/datasets", headers,
                        {"name": DATASET_NAME, "description": DESCRIPTION})
    if not 200 <= status < 300:
        raise RuntimeError(f"creating dataset: {status} {text}")

    errors = []
    for case in suite:
        status, text = call("POST", f"{host}/api/public/dataset-items", headers, item(case))
        if not 200 <= status < 300:
            errors.append(f"{case.id}: {status} {text}")

    stale = existing_ids(host, headers, call) - {c.id for c in suite}
    return {"synced": len(suite) - len(errors), "errors": errors, "stale": sorted(stale)}


def require(suite, call=http) -> str:
    """The dataset's fuse id, once every case about to run has an item.

    An experiment trace whose item is missing would not link to the dataset,
    so this fails before the run rather than leaving gaps in the comparison.
    """
    host, headers = fuse()
    status, text = call("GET", f"{host}/api/public/v2/datasets/{DATASET_NAME}", headers)
    if not 200 <= status < 300:
        raise RuntimeError(f"no fuse dataset {DATASET_NAME} ({status}): "
                           "run `uv run python -m evals.dataset` first")
    missing = {c.id for c in suite} - existing_ids(host, headers, call)
    if missing:
        raise RuntimeError(f"{DATASET_NAME} has no item for {', '.join(sorted(missing))}: "
                           "run `uv run python -m evals.dataset` first")
    return json.loads(text)["id"]


def main(argv=None) -> int:
    argparse.ArgumentParser(description=f"Sync the gold cases to fuse dataset {DATASET_NAME}.") \
        .parse_args(argv)
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    report = sync(case_loader.load_cases())
    print(f"{DATASET_NAME}: {report['synced']} items synced")
    for error in report["errors"]:
        print(f"  failed {error}")
    if report["stale"]:
        print(f"  in fuse but not in evals/cases/: {', '.join(report['stale'])}")
    return 1 if report["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
