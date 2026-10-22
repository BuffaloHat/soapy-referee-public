"""The runner turns a model reply into a verdict, and writes down what happened.

The model is faked throughout — these tests are about the decision logic, not
about Ollama. Everything downstream of the reply is real: real SQL, real
snapshot, real comparator.
"""

import json

import pytest

from agent import llm
from evals import cases as case_loader
from evals import comparator, config, db, record, resolve, runner


@pytest.fixture(scope="module")
def con():
    return db.connect()


@pytest.fixture(scope="module")
def resolver(con):
    return resolve.Resolver.from_snapshot(con)


@pytest.fixture(scope="module")
def suite():
    return {c.id: c for c in case_loader.load_cases()}


@pytest.fixture
def cfg():
    return config.load("local-columns")


def say(monkeypatch, text, **kwargs):
    """Make the model reply with exactly this."""
    completion = llm.Completion(
        text=text, model="fake", prompt_tokens=kwargs.get("prompt_tokens", 100),
        output_tokens=kwargs.get("output_tokens", 10), elapsed_s=0.01,
        done_reason=kwargs.get("done_reason", "stop"),
    )
    monkeypatch.setattr(runner.llm, "generate", lambda *a, **k: completion)


def answering(suite):
    return next(c for c in suite.values() if c.expects == "rows" and c.check_sql)


def refusing(suite):
    return next(c for c in suite.values() if c.expects == "refusal")


# --- verdicts --------------------------------------------------------------

def test_a_right_answer_is_exact(monkeypatch, con, cfg, resolver, suite):
    # check_sql is the same answer by another route, so it must score exact.
    case = answering(suite)
    say(monkeypatch, case.check_sql)
    row = runner.run_case(con, cfg, case, resolver, "schema", attempt=1)
    assert row.verdict == comparator.EXACT, row.detail
    assert row.gold_rows == row.agent_rows == case.cardinality
    assert row.sql


def test_a_wrong_answer_is_not_passing(monkeypatch, con, cfg, resolver, suite):
    case = answering(suite)
    say(monkeypatch, "SELECT 999999 AS nonsense")
    row = runner.run_case(con, cfg, case, resolver, "schema", attempt=1)
    assert row.verdict not in runner.PASSING


def test_broken_agent_sql_is_an_error_not_a_wrong_answer(monkeypatch, con, cfg, resolver, suite):
    # A query that will not run says nothing about the answer.
    say(monkeypatch, "SELECT * FROM no_such_table")
    row = runner.run_case(con, cfg, answering(suite), resolver, "schema", attempt=1)
    assert row.verdict == comparator.ERROR
    assert row.detail == "agent SQL failed"
    assert row.error


def test_a_rejected_statement_is_an_error(monkeypatch, con, cfg, resolver, suite):
    # The DB guard refuses non-SELECT; the agent never touches the snapshot.
    say(monkeypatch, "DROP TABLE game")
    row = runner.run_case(con, cfg, answering(suite), resolver, "schema", attempt=1)
    assert row.verdict == comparator.ERROR


def test_an_unreachable_model_is_an_error(monkeypatch, con, cfg, resolver, suite):
    def boom(*a, **k):
        raise llm.LLMError("cannot reach ollama")

    monkeypatch.setattr(runner.llm, "generate", boom)
    row = runner.run_case(con, cfg, answering(suite), resolver, "schema", attempt=1)
    assert row.verdict == comparator.ERROR
    assert "cannot reach" in row.error


# --- the refusal 2x2 -------------------------------------------------------

def test_declining_a_refusal_case_passes(monkeypatch, con, cfg, resolver, suite):
    say(monkeypatch, "CANNOT ANSWER: the database holds no salary data")
    row = runner.run_case(con, cfg, refusing(suite), resolver, "schema", attempt=1)
    assert row.verdict == runner.REFUSAL_CORRECT
    assert row.verdict in runner.PASSING
    assert row.refusal


def test_answering_a_refusal_case_fails(monkeypatch, con, cfg, resolver, suite):
    say(monkeypatch, "SELECT count(*) FROM game")
    row = runner.run_case(con, cfg, refusing(suite), resolver, "schema", attempt=1)
    assert row.verdict == runner.REFUSAL_ABSENT
    assert row.verdict not in runner.PASSING
    # The invented query still runs, because whether it runs is the finding.
    assert "answered anyway" in row.detail


def test_an_invented_query_that_does_not_run_is_still_recorded(
        monkeypatch, con, cfg, resolver, suite):
    say(monkeypatch, "SELECT salary FROM player_salaries")
    row = runner.run_case(con, cfg, refusing(suite), resolver, "schema", attempt=1)
    assert row.verdict == runner.REFUSAL_ABSENT
    assert "query failed" in row.detail


def test_declining_an_answerable_case_fails(monkeypatch, con, cfg, resolver, suite):
    say(monkeypatch, "CANNOT ANSWER: not sure")
    row = runner.run_case(con, cfg, answering(suite), resolver, "schema", attempt=1)
    assert row.verdict == runner.REFUSAL_UNEXPECTED
    assert row.verdict not in runner.PASSING


# --- what lands in the record ----------------------------------------------

def test_the_row_carries_what_the_model_actually_said(monkeypatch, con, cfg, resolver, suite):
    case = answering(suite)
    say(monkeypatch, f"```sql\n{case.check_sql}\n```", prompt_tokens=2412, output_tokens=42)
    row = runner.run_case(con, cfg, case, resolver, "schema", attempt=1)
    assert row.raw_response.startswith("```sql")   # verbatim, fences and all
    assert not row.sql.startswith("```")           # cleaned for execution
    assert row.prompt_tokens == 2412
    assert row.output_tokens == 42
    assert row.done_reason == "stop"


def test_a_cut_off_reply_keeps_its_done_reason(monkeypatch, con, cfg, resolver, suite):
    say(monkeypatch, "SELECT 1", done_reason="length")
    row = runner.run_case(con, cfg, answering(suite), resolver, "schema", attempt=1)
    assert row.done_reason == "length"


def say_in_turn(monkeypatch, *texts):
    """The model replies with each text in order, one per call."""
    replies = iter(texts)
    def generate(*args, **kwargs):
        return llm.Completion(text=next(replies), model="fake", prompt_tokens=100,
                                 output_tokens=10, elapsed_s=0.01, done_reason="stop")
    monkeypatch.setattr(runner.llm, "generate", generate)


def with_retries(cfg, n=1):
    return config.from_dict(cfg.to_header() | {"sql_retries": n})


def test_no_retry_unless_configured(monkeypatch, con, cfg, resolver, suite):
    say_in_turn(monkeypatch, "SELECT * FROM no_such_table", "SELECT 1")
    row = runner.run_case(con, cfg, answering(suite), resolver, "schema", attempt=1)
    assert (row.verdict, row.retries, row.first_sql) == (comparator.ERROR, 0, None)


def test_a_retry_can_fix_a_failed_query(monkeypatch, con, cfg, resolver, suite):
    case = answering(suite)
    say_in_turn(monkeypatch, "SELECT * FROM no_such_table", case.check_sql)
    row = runner.run_case(con, with_retries(cfg), case, resolver, "schema", attempt=1)
    assert row.verdict == comparator.EXACT, row.detail
    assert row.retries == 1
    # What was scored, and what the retry fixed.
    assert row.sql == case.check_sql.strip().rstrip(";")
    assert row.first_sql == "SELECT * FROM no_such_table" and "no_such_table" in row.first_error
    assert (row.prompt_tokens, row.output_tokens) == (200, 20)     # both calls


def test_a_retry_that_fails_again_is_still_an_error(monkeypatch, con, cfg, resolver, suite):
    say_in_turn(monkeypatch, "SELECT * FROM no_such_table", "SELECT * FROM still_missing")
    row = runner.run_case(con, with_retries(cfg), answering(suite), resolver, "schema", attempt=1)
    assert (row.verdict, row.detail, row.retries) == (comparator.ERROR, "agent SQL failed", 1)
    assert "still_missing" in row.error and "no_such_table" in row.first_error


def test_declining_on_the_retry_is_an_unexpected_refusal(monkeypatch, con, cfg, resolver, suite):
    say_in_turn(monkeypatch, "SELECT * FROM no_such_table", "CANNOT ANSWER: not in the data")
    row = runner.run_case(con, with_retries(cfg), answering(suite), resolver, "schema", attempt=1)
    assert (row.verdict, row.retries) == (runner.REFUSAL_UNEXPECTED, 1)


def test_the_answer_is_written_from_the_agents_rows(monkeypatch, con, cfg, resolver, suite):
    case = answering(suite)
    say_in_turn(monkeypatch, case.check_sql, "  The answer is 72.  ")
    with_answer = config.from_dict(cfg.to_header() | {"write_answer": True})
    row = runner.run_case(con, with_answer, case, resolver, "schema", attempt=1)
    assert row.verdict == comparator.EXACT
    assert row.answer == "The answer is 72."
    # Answers are graded separately; they never touch the SQL verdict.
    assert (row.prompt_tokens, row.output_tokens) == (100, 10)


def test_no_answer_unless_configured_or_when_the_sql_failed(monkeypatch, con, cfg, resolver, suite):
    say_in_turn(monkeypatch, "SELECT * FROM no_such_table", "should not be asked")
    with_answer = config.from_dict(cfg.to_header() | {"write_answer": True})
    row = runner.run_case(con, with_answer, answering(suite), resolver, "schema", attempt=1)
    assert row.answer is None
    say_in_turn(monkeypatch, answering(suite).check_sql)
    assert runner.run_case(con, cfg, answering(suite), resolver, "schema", attempt=1).answer is None


def test_the_row_shows_both_answers(monkeypatch, con, cfg, resolver, suite):
    # What a person checks first: the two answers side by side.
    case = answering(suite)
    say(monkeypatch, "SELECT NULL AS avg_points")
    row = runner.run_case(con, cfg, case, resolver, "schema", attempt=1)
    assert row.agent_preview == {"columns": ["avg_points"], "rows": [[None]],
                                 "total": 1, "truncated": False}
    assert row.gold_preview["total"] == case.cardinality
    assert len(row.gold_preview["rows"]) == min(case.cardinality, record.PREVIEW_ROWS)
    json.dumps(row.gold_preview)                   # fit for the run record as is


def test_a_failed_agent_query_still_shows_the_gold_answer(monkeypatch, con, cfg, resolver, suite):
    say(monkeypatch, "SELECT * FROM no_such_table")
    row = runner.run_case(con, cfg, answering(suite), resolver, "schema", attempt=1)
    assert row.gold_preview["rows"] and row.agent_preview is None


def test_a_preview_reads_as_a_small_table():
    result = db.QueryResult(columns=["team", "avg"], rows=[(f"T{i}", None) for i in range(7)],
                            truncated=False, elapsed_s=0.0)
    lines = record.preview_text(record.preview(result)).splitlines()
    assert lines[:3] == ["7 rows", "team | avg", "T0 | NULL"]
    assert lines[-1] == "… 2 more"
    assert len(lines) == 2 + record.PREVIEW_ROWS + 1


# --- the record itself -----------------------------------------------------

def test_the_record_is_written_as_it_goes(tmp_path, cfg):
    run = record.RunRecord.start(cfg, case_count=2, snapshot_sha="abc",
                                 schema_block_chars=10, runs_dir=tmp_path)
    assert (run.directory / record.HEADER_FILE).exists()

    run.append(record.CaseRow(case_id="a", tier="baseline", expects="rows",
                              attempt=1, verdict="exact"))
    # Readable mid-run: a crash at case eight must not cost cases one to seven.
    _, rows = record.load(run.directory)
    assert len(rows) == 1 and rows[0]["case_id"] == "a"

    run.append(record.CaseRow(case_id="b", tier="trap", expects="rows",
                              attempt=1, verdict="wrong"))
    run.finish()

    header, rows = record.load(run.directory)
    assert len(rows) == 2
    assert header["finished_at"]
    assert header["verdicts"] == {"exact": 1, "wrong": 1}


def test_the_header_says_which_snapshot_and_which_code(tmp_path, cfg):
    run = record.RunRecord.start(cfg, case_count=1, snapshot_sha="deadbeef",
                                 schema_block_chars=42, runs_dir=tmp_path)
    run.finish()
    header, _ = record.load(run.directory)
    # A verdict is meaningless without the snapshot and settings behind it.
    assert header["snapshot_sha256"] == "deadbeef"
    assert header["config"] == cfg.to_header()
    assert config.from_dict(header["config"]) == cfg
    assert "git_commit" in header
    assert header["environment"] == "dev"           # SREF_ENV unset in tests


def test_every_row_is_one_json_line(tmp_path, cfg):
    run = record.RunRecord.start(cfg, case_count=1, snapshot_sha="x",
                                 schema_block_chars=1, runs_dir=tmp_path)
    run.append(record.CaseRow(case_id="a", tier="trap", expects="rows", attempt=1,
                              verdict="wrong", raw_response="line one\nline two"))
    text = (run.directory / record.CASES_FILE).read_text()
    assert len(text.strip().splitlines()) == 1     # embedded newlines stay escaped
    assert json.loads(text)["raw_response"] == "line one\nline two"


def test_latest_finds_the_newest_run(tmp_path, cfg):
    assert record.latest(tmp_path) is None
    first = record.RunRecord.start(cfg, 1, "x", 1, runs_dir=tmp_path)
    first.finish()
    assert record.latest(tmp_path) == first.directory


# --- the suite -------------------------------------------------------------

def test_an_unknown_case_id_stops_the_run(cfg):
    with pytest.raises(SystemExit, match="no such case"):
        runner.run_suite(cfg, selected=["not-a-case"], verbose=False)


def test_one_bad_case_does_not_end_the_run(monkeypatch, tmp_path, cfg, suite):
    ids = sorted(suite)[:2]

    def explode(*a, **k):
        raise RuntimeError("unexpected")

    monkeypatch.setattr(runner, "run_case", explode)
    monkeypatch.setattr(runner.llm, "preflight", lambda *a, **k: llm.Completion(
        "ok", "fake", 10, 1, 0.0, "stop"))
    run = runner.run_suite(cfg, selected=ids, runs_dir=tmp_path, verbose=False)
    assert len(run.rows) == 2
    assert all(r.verdict == comparator.ERROR for r in run.rows)
    assert all("unexpected" in r.error for r in run.rows)


# --- attempts and the noise floor ------------------------------------------

def test_attempt_one_is_always_the_unperturbed_prompt():
    # So attempt 1 of an r3 run is directly comparable with a single-attempt run.
    single = config.load("local-docs")
    repeated = config.load("local-docs-r3")
    assert runner.schema_for(repeated, 1) == runner.schema_for(single, 1)


def test_later_attempts_each_get_their_own_table_order():
    cfg = config.load("local-docs-r3")
    blocks = [runner.schema_for(cfg, a) for a in range(1, cfg.repeats + 1)]
    assert len(set(blocks)) == cfg.repeats


def test_no_perturbation_means_no_shuffle():
    cfg = config.load("local-docs")
    assert runner.shuffle_seed(cfg, 2) is None


def _row(case_id, attempt, verdict):
    return record.CaseRow(case_id=case_id, tier="trap", expects="rows",
                          attempt=attempt, verdict=verdict)


def test_stability_separates_a_verdict_wobble_from_a_pass_flip():
    rows = [
        _row("steady", 1, "wrong"), _row("steady", 2, "wrong"), _row("steady", 3, "wrong"),
        # Moves between two passing verdicts: changed, but the score never feels it.
        _row("wobble", 1, "exact"), _row("wobble", 2, "tolerance"), _row("wobble", 3, "exact"),
        _row("flip", 1, "exact"), _row("flip", 2, "wrong"), _row("flip", 3, "exact"),
    ]
    summary = runner.stability(rows)
    assert summary["verdict_changed"] == 2
    assert summary["pass_changed"] == 1
    assert summary["cases"]["flip"] == {
        "passes": 2, "attempts": 3, "verdicts": ["exact", "wrong", "exact"]}


def test_a_repeated_run_records_each_attempt_order_and_its_stability(
        monkeypatch, tmp_path, suite):
    cfg = config.load("local-columns-r3")
    ids = sorted(suite)[:2]
    seen = []

    def fake_case(con, cfg, case, resolver, schema_block, attempt, run_id=""):
        seen.append((attempt, schema_block))
        return _row(case.id, attempt, "exact" if attempt != 2 else "wrong")

    monkeypatch.setattr(runner, "run_case", fake_case)
    monkeypatch.setattr(runner.llm, "preflight", lambda *a, **k: llm.Completion(
        "ok", "fake", 10, 1, 0.0, "stop"))
    run = runner.run_suite(cfg, selected=ids, runs_dir=tmp_path, verbose=False)

    header, rows = record.load(run.directory)
    assert len(rows) == 6
    assert sorted(header["schema_orders"]) == ["1", "2", "3"]
    assert len({tuple(order) for order in header["schema_orders"].values()}) == 3
    # Each case sees its own attempt's block, and the block is constant within an attempt.
    assert len({block for _, block in seen}) == 3
    assert header["stability"]["pass_changed"] == 2


# --- which code produced the run --------------------------------------------

def _fake_git(monkeypatch, status: str):
    outputs = {"rev-parse": "4032c6d\n", "status": status}

    def run(cmd, **kwargs):
        return type("Done", (), {"returncode": 0, "stdout": outputs[cmd[3]]})()

    monkeypatch.setattr(record.subprocess, "run", run)


def test_a_clean_tree_is_labelled_by_its_commit(monkeypatch):
    _fake_git(monkeypatch, status="")
    assert record.git_commit() == "4032c6d"
    assert record.git_dirty() is False


def test_uncommitted_changes_mark_the_commit_dirty(monkeypatch):
    # A bare hash would claim the run can be rebuilt from that commit.
    _fake_git(monkeypatch, status=" M evals/tracing.py\n")
    assert record.git_commit() == "4032c6d-dirty"
    assert record.git_dirty() is True
