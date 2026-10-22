"""The gold set reaches fuse as dataset items — questions only, never answers."""

import json

import pytest

from evals import cases as case_loader
from evals import dataset


@pytest.fixture
def fuse_env(monkeypatch):
    monkeypatch.setenv("LANGFUSE_HOST", "http://fuse")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk")


def fake_fuse(listed=(), fail=()):
    """Records every request; lists `listed` as the dataset's items."""
    calls = []

    def call(method, url, headers, body=None):
        calls.append({"method": method, "url": url, "body": body})
        if method == "GET":
            data = [{"id": i} for i in listed]
            return 200, json.dumps({"data": data, "meta": {"totalPages": 1}})
        if body and body.get("id") in fail:
            return 400, "bad item"
        return 200, "{}"

    return call, calls


def test_one_item_per_case_keyed_by_case_id(fuse_env):
    suite = case_loader.load_cases()
    call, calls = fake_fuse(listed=[c.id for c in suite])
    report = dataset.sync(suite, call)
    items = [c["body"] for c in calls if c["url"].endswith("/dataset-items")]
    assert [i["id"] for i in items] == [c.id for c in suite]
    assert report == {"synced": len(suite), "errors": [], "stale": []}


def test_an_item_holds_the_question_and_no_answer():
    # Gold is executed, not stored: the expected answer is attached at run time.
    case = next(c for c in case_loader.load_cases() if c.answerable)
    body = dataset.item(case)
    assert body["input"] == case.question
    assert "expectedOutput" not in body
    assert case.gold_sql.strip() not in json.dumps(body)
    assert body["metadata"] == {"case_id": case.id, "tier": case.tier, "expects": case.expects}


def test_the_dataset_is_created_before_its_items(fuse_env):
    call, calls = fake_fuse()
    dataset.sync(case_loader.load_cases()[:1], call)
    assert calls[0]["url"].endswith("/api/public/v2/datasets")
    assert calls[0]["body"]["name"] == dataset.DATASET_NAME


def test_items_with_no_case_are_reported_not_deleted(fuse_env):
    suite = case_loader.load_cases()
    call, calls = fake_fuse(listed=[c.id for c in suite] + ["retired-case"])
    assert dataset.sync(suite, call)["stale"] == ["retired-case"]
    assert not any(c["method"] == "DELETE" for c in calls)


def test_a_failed_item_does_not_stop_the_rest(fuse_env):
    suite = case_loader.load_cases()
    call, _ = fake_fuse(fail={suite[0].id})
    report = dataset.sync(suite, call)
    assert report["synced"] == len(suite) - 1
    assert report["errors"][0].startswith(suite[0].id)


def test_an_experiment_needs_every_case_to_have_an_item(fuse_env):
    suite = case_loader.load_cases()
    call, _ = fake_fuse(listed=[c.id for c in suite[1:]])
    with pytest.raises(RuntimeError, match=suite[0].id):
        dataset.require(suite, call)


def test_an_experiment_gets_the_datasets_fuse_id(fuse_env):
    suite = case_loader.load_cases()
    listed = [{"id": c.id} for c in suite]

    def call(method, url, headers, body=None):
        if url.endswith(f"/v2/datasets/{dataset.DATASET_NAME}"):
            return 200, json.dumps({"id": "ds-1", "name": dataset.DATASET_NAME})
        return 200, json.dumps({"data": listed, "meta": {"totalPages": 1}})

    assert dataset.require(suite, call) == "ds-1"


def test_a_missing_dataset_says_how_to_make_it(fuse_env):
    def call(method, url, headers, body=None):
        return 404, "not found"

    with pytest.raises(RuntimeError, match="evals.dataset"):
        dataset.require(case_loader.load_cases(), call)
