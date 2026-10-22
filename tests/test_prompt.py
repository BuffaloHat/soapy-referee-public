"""The prompt contract holds both ways: what we ask for, and what we accept back."""

import pytest

from agent import prompt, schema_context
from evals import cases, config


@pytest.fixture
def cfg():
    return config.load("local-columns")


# --- versions --------------------------------------------------------------

def test_v1_exists():
    assert "v1" in prompt.available()


def test_a_missing_version_stops_the_run(cfg):
    # A silent fallback would credit a run to a prompt it never used.
    bad = config.from_dict({**cfg.to_header(), "prompt_version": "v99"})
    with pytest.raises(prompt.PromptError, match="no prompt version"):
        prompt.build(bad, "how many games?", "schema")


def test_every_shipped_config_names_a_real_prompt():
    for name in config.available():
        prompt.load(config.load(name).prompt_version)


# --- building --------------------------------------------------------------

def test_build_puts_schema_before_question(cfg):
    built = prompt.build(cfg, "  how many games?  ", "SCHEMA-BLOCK")
    assert built.user.index("SCHEMA-BLOCK") < built.user.index("how many games?")
    assert built.version == "v1"
    assert built.system


def test_the_refusal_marker_the_prompt_asks_for_is_the_one_we_parse(cfg):
    # If these drift apart, every refusal case silently becomes a failure.
    assert prompt.REFUSAL_MARKER in prompt.load(cfg.prompt_version)


# --- parsing ---------------------------------------------------------------

def test_plain_sql_comes_back_as_sql():
    reply = prompt.parse("SELECT count(*) FROM game;")
    assert reply.sql == "SELECT count(*) FROM game"
    assert not reply.refused


@pytest.mark.parametrize("wrapped", [
    "```sql\nSELECT 1\n```",
    "```\nSELECT 1\n```",
    "  ```sql\nSELECT 1\n```  ",
])
def test_fenced_sql_is_unwrapped_not_rejected(wrapped):
    # Badly formatted is not the same as wrong; scoring it as failure would
    # measure instruction-following rather than text-to-SQL.
    assert prompt.parse(wrapped).sql == "SELECT 1"


def test_multiline_sql_survives_intact():
    sql = "SELECT team_id_home,\n       count(*)\nFROM game\nGROUP BY 1"
    assert prompt.parse(sql).sql == sql


def test_a_refusal_is_recognised():
    reply = prompt.parse("CANNOT ANSWER: the database holds no salary data")
    assert reply.refused
    assert reply.refusal == "the database holds no salary data"
    assert reply.sql is None


def test_a_refusal_is_recognised_through_formatting():
    for text in [
        "cannot answer: no salary data",
        "```\nCANNOT ANSWER: no salary data\n```",
        "CANNOT ANSWER - no salary data",
    ]:
        assert prompt.parse(text).refused, text


def test_the_marker_inside_a_query_is_not_a_refusal():
    # A real attempt that happens to mention the phrase must not be scored as
    # a decline. Only the opening line counts.
    sql = "SELECT count(*) FROM game -- CANNOT ANSWER precisely without a date"
    reply = prompt.parse(sql)
    assert not reply.refused
    assert reply.sql.startswith("SELECT")


def test_a_refusal_without_a_reason_still_parses():
    assert prompt.parse("CANNOT ANSWER").refusal == "(no reason given)"


def test_an_empty_reply_is_an_error_not_a_refusal():
    # Nothing at all is a broken call, not a considered decline.
    with pytest.raises(prompt.PromptError, match="empty reply"):
        prompt.parse("   \n  ")


def test_the_raw_reply_is_always_kept():
    # The run record stores what the model actually said, not our reading of it.
    assert prompt.parse("```sql\nSELECT 1\n```").raw == "```sql\nSELECT 1\n```"


def test_a_retry_keeps_the_prompt_and_adds_only_the_failure(cfg):
    built = prompt.build(cfg, "How many?", "SCHEMA")
    retry = prompt.build_retry(built, "SELECT x FROM t", "Binder Error: no column x")
    assert retry.system == built.system
    # Same prefix, so the model's cache is reused; then the query and the error.
    assert retry.user.startswith(built.user)
    added = retry.user[len(built.user):]
    assert "SELECT x FROM t" in added and "Binder Error: no column x" in added
    assert prompt.REFUSAL_MARKER in added


def test_the_answer_call_sees_the_question_and_rows_only():
    built = prompt.build_answer("v1", "How many?", "1 row\ngames\n72")
    assert built.user == "Question: How many?\n\nQuery result:\n1 row\ngames\n72"
    assert "SELECT" not in built.user.upper()


# --- leak tests (overview.md, principle 2) ---------------------------------

def _texts(version):
    """The system prompt, its retry follow-up and the answer prompt: all reach a model."""
    texts = [prompt.load(version)]
    if (prompt.RETRY_DIR / f"{version}.md").exists():
        texts.append(prompt.load_retry(version))
    if (prompt.ANSWER_DIR / f"{version}.md").exists():
        texts.append(prompt.build_answer(version, "", "").system)
    return [t.lower() for t in texts]

@pytest.mark.parametrize("version", prompt.available())
def test_no_gold_sql_or_case_question_reaches_the_prompt(version):
    for text in _texts(version):
        for case in cases.load_cases():
            assert case.question.lower() not in text
            for sql in (case.gold_sql, case.check_sql):
                if sql:
                    for clause in ("group by", "order by", "having"):
                        index = sql.lower().find(clause)
                        if index != -1:
                            assert sql.lower()[index:index + 40] not in text


@pytest.mark.parametrize("version", prompt.available())
def test_the_prompt_names_no_table_or_column(version):
    """It says what shape the answer takes. The schema block says what exists.

    A table name in the prompt would be a hint that survives into the
    `columns` arm, quietly contaminating the control.
    """
    for text in _texts(version):
        for table in schema_context.table_names():
            assert table not in text
