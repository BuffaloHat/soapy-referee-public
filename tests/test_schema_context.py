"""The schema block states facts, and the two modes really are different arms.

The leak tests here are the Phase 3 guardrail: folder
separation keeps eval material out of `agent/`, and these assert it stayed out.
"""

import pytest

from agent import schema_context as sc
from evals import cases


@pytest.fixture(scope="module")
def columns_block() -> str:
    return sc.build("columns")


@pytest.fixture(scope="module")
def docs_block() -> str:
    return sc.build("docs")


def test_unknown_mode_is_rejected():
    with pytest.raises(sc.SchemaError, match="unknown schema mode"):
        sc.build("everything")


def test_missing_catalog_is_rejected(tmp_path):
    with pytest.raises(sc.SchemaError, match="no schema catalog"):
        sc.build("columns", path=str(tmp_path / "nope.yaml"))


def test_every_table_appears_in_both_modes(columns_block, docs_block):
    for name in sc.table_names():
        assert f"CREATE TABLE {name} (" in columns_block
        assert f"TABLE {name} —" in docs_block


def test_columns_mode_carries_names_and_types_only(columns_block):
    # No stats, no samples: the control arm is bare DDL.
    assert "populated" not in columns_block
    assert "distinct" not in columns_block
    assert "values" not in columns_block


def test_docs_mode_carries_the_measured_facts(docs_block):
    assert "populated" in docs_block
    assert "distinct" in docs_block
    # Season codes are the encoding the model guessed wrong unaided.
    assert "season_id" in docs_block


def test_a_complete_value_set_is_labelled_as_such(docs_block):
    # wl_home has two values; the whole set is the encoding.
    line = _column_line(docs_block, "game", "wl_home")
    assert "values 'L', 'W'" in line
    assert "range" not in line


def test_a_sampled_column_shows_its_range_not_just_examples(docs_block):
    # The bug this replaced: three sorted preseason codes, no sign of the
    # regular-season prefix. The range has to span the encoding.
    line = _column_line(docs_block, "game", "season_id")
    assert "range" in line and "e.g." in line
    assert "'42022'" in line


def test_num_ctx_leaves_room_for_the_largest_schema_block():
    # Ollama drops overflow silently: too small a window is a wrong answer
    # with no error. Guard the setting against the block it has to hold.
    from evals import config

    # Measured, not guessed: the docs block is 46,312 chars and Ollama read it
    # as 16,926 tokens on 2026-09-22 — 2.74 chars per token. The usual chars/4
    # prose heuristic understates this block by 46%, because it is mostly
    # quoted identifiers, separators and numbers. 2.0 keeps a margin under that.
    CHARS_PER_TOKEN = 2.0

    biggest = max(len(sc.build(mode)) for mode in sc.MODES)
    estimated_tokens = biggest / CHARS_PER_TOKEN
    for name in config.available():
        cfg = config.load(name)
        assert cfg.num_ctx > 1.25 * estimated_tokens, (
            f"{name}: num_ctx {cfg.num_ctx} is too tight for a ~{estimated_tokens:,.0f}-token block"
        )


def _column_line(block: str, table: str, column: str) -> str:
    inside = False
    for line in block.splitlines():
        if line.startswith("TABLE "):
            inside = line.startswith(f"TABLE {table} —")
        elif inside and line.strip().startswith(f"{column} "):
            return line
    raise AssertionError(f"{table}.{column} not found")


def test_docs_mode_is_the_bigger_arm(columns_block, docs_block):
    # If the two arms were the same size, the experiment would be measuring nothing.
    assert len(docs_block) > 2 * len(columns_block)


def test_the_empty_table_is_reported_honestly(docs_block):
    # team_info_common has no rows. Saying so is a fact; warning against it is a hint.
    assert "TABLE team_info_common — 0 rows" in docs_block


# --- Leak tests (overview.md, principle 2) ---------------------------------

# Instructional language: the mark of "how to query" rather than "what is here".
INSTRUCTIONAL = [
    "trap", "careful", "caution", "warning", "beware", "watch out",
    "make sure", "be sure", "you should", "should use", "instead of",
    "note that", "remember to", "avoid", "don't use", "do not use",
    "prefer", "correct query", "the right way",
]


@pytest.mark.parametrize("mode", sc.MODES)
@pytest.mark.parametrize("phrase", INSTRUCTIONAL)
def test_schema_block_gives_no_query_advice(mode, phrase):
    assert phrase not in sc.build(mode).lower()


@pytest.mark.parametrize("mode", sc.MODES)
def test_no_gold_sql_reaches_the_agent(mode):
    block = sc.build(mode).lower()
    for case in cases.load_cases():
        for sql in (case.gold_sql, case.check_sql):
            if not sql:
                continue
            # Any distinctive clause from a gold query appearing verbatim in the
            # schema block would be the answer key leaking through.
            for clause in ("group by", "order by", "having"):
                fragment = _clause_fragment(sql.lower(), clause)
                if fragment:
                    assert fragment not in block


@pytest.mark.parametrize("mode", sc.MODES)
def test_no_case_question_reaches_the_agent(mode):
    block = sc.build(mode).lower()
    for case in cases.load_cases():
        assert case.question.lower() not in block


def _clause_fragment(sql: str, clause: str) -> str:
    """The 40 characters following a clause keyword, as a fingerprint of the query."""
    index = sql.find(clause)
    if index == -1:
        return ""
    return sql[index:index + 40]


# --- table-order perturbation ----------------------------------------------

def _table_blocks(block: str) -> set[str]:
    return set(block.split("\n\n"))


@pytest.mark.parametrize("mode", sc.MODES)
def test_a_shuffle_moves_whole_tables_and_nothing_else(mode):
    # Same facts, different order: every table block is unchanged, only its
    # position moves, so the shuffled prompt cannot say anything new.
    plain = sc.build(mode)
    shuffled = sc.build(mode, shuffle_seed=2)
    assert shuffled != plain
    assert _table_blocks(shuffled) == _table_blocks(plain)
    assert len(shuffled) == len(plain)


def test_a_shuffle_is_reproducible_from_its_seed():
    assert sc.build("docs", shuffle_seed=2) == sc.build("docs", shuffle_seed=2)
    assert sc.table_names(shuffle_seed=2) != sc.table_names(shuffle_seed=3)


def test_table_names_follow_the_rendered_order():
    block = sc.build("columns", shuffle_seed=3)
    rendered = [line.split()[2] for line in block.splitlines() if line.startswith("CREATE TABLE")]
    assert rendered == sc.table_names(shuffle_seed=3)
