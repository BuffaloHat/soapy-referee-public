"""The prompt contract: what the agent is asked for, and how the reply is read.

Building and parsing live together on purpose. The reply format is defined by
the prompt text, so a change to one is a change to the other, and splitting
them across files is how a parser ends up looking for a format nobody asks for
any more.

Prompt text is versioned in `agent/prompts/<version>.md` and selected by the
run config, so a prompt change is a run setting rather than an edit to source.

Nothing here states how to query: no hints, no worked examples, no warnings
about the data. The prompt says what shape the answer takes; the question and
the schema say everything else.
"""

import re
from dataclasses import dataclass
from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
# The follow-up sent after a query fails, one per prompt version.
RETRY_DIR = PROMPTS_DIR / "retry"
# The second call: turn the query result into a sentence a person would read.
ANSWER_DIR = PROMPTS_DIR / "answer"

# The agent declines by starting its reply with this. A refusal has no rows to
# compare, so the runner has to recognise one without asking a model.
REFUSAL_MARKER = "CANNOT ANSWER"

_FENCE = re.compile(r"^```[a-zA-Z]*\s*\n(.*?)\n?```\s*$", re.DOTALL)


class PromptError(Exception):
    """The prompt version does not exist, or the reply cannot be read."""


@dataclass(frozen=True)
class Prompt:
    system: str
    user: str
    version: str


@dataclass(frozen=True)
class Reply:
    """What the model came back with, read against the contract."""

    sql: str | None
    refusal: str | None
    raw: str

    @property
    def refused(self) -> bool:
        return self.refusal is not None


def available() -> list[str]:
    return sorted(p.stem for p in PROMPTS_DIR.glob("*.md"))


def load(version: str) -> str:
    """The system prompt for one version. Missing means stop, not fall back.

    A silent default would attribute a run to a prompt it never used.
    """
    path = PROMPTS_DIR / f"{version}.md"
    if not path.exists():
        raise PromptError(
            f"no prompt version '{version}' in {PROMPTS_DIR.name}/ — have {available()}"
        )
    return path.read_text().strip()


def build(config, question: str, schema_block: str) -> Prompt:
    """Assemble the call: system prompt from the version, schema then question."""
    return Prompt(
        system=load(config.prompt_version),
        user=f"{schema_block}\n\nQuestion: {question.strip()}",
        version=config.prompt_version,
    )


def load_retry(version: str) -> str:
    path = RETRY_DIR / f"{version}.md"
    if not path.exists():
        raise PromptError(f"no retry prompt for version '{version}' in prompts/retry/")
    return path.read_text().strip()


def build_retry(built: Prompt, sql: str, error: str) -> Prompt:
    """The follow-up after a query fails: the same prompt, then the failed
    query and the database's own error message.

    Nothing is added beyond what the database said — no diagnosis, no hint —
    so a retry measures whether the model can use an error, not whether we can
    tell it the answer. The original prompt stays as the prefix, so Ollama
    reuses its cache and a retry costs about one warm call.
    """
    note = load_retry(built.version).replace("{sql}", sql).replace("{error}", error)
    return Prompt(system=built.system, user=f"{built.user}\n\n{note}", version=built.version)


def build_answer(version: str, question: str, result_text: str) -> Prompt:
    """The prose-answer call: the question and the rows, nothing else.

    No schema and no SQL, so the answer can only come from the rows. That is
    what makes it checkable: a faithful answer says what the rows say.
    """
    path = ANSWER_DIR / f"{version}.md"
    if not path.exists():
        raise PromptError(f"no answer prompt for version '{version}' in prompts/answer/")
    return Prompt(system=path.read_text().strip(),
                  user=f"Question: {question.strip()}\n\nQuery result:\n{result_text}",
                  version=version)


def parse(text: str) -> Reply:
    """Read a reply against the contract: one SELECT, or a refusal.

    Fences are stripped rather than rejected. A model wrapping SQL in ```sql
    has answered the question and formatted it badly, and scoring that as a
    failure would measure instruction-following, not text-to-SQL.
    """
    raw = text
    cleaned = text.strip()

    fenced = _FENCE.match(cleaned)
    if fenced:
        cleaned = fenced.group(1).strip()

    if not cleaned:
        raise PromptError("empty reply")

    # Only the opening line counts. Searching the whole reply would read a
    # query carrying `-- cannot answer precisely` in a comment as a refusal,
    # and score a real attempt as a decline.
    first_line = cleaned.splitlines()[0].strip()
    if first_line.upper().startswith(REFUSAL_MARKER):
        reason = first_line[len(REFUSAL_MARKER):].lstrip(" :-—").strip()
        return Reply(sql=None, refusal=reason or "(no reason given)", raw=raw)

    return Reply(sql=cleaned.rstrip(";").strip(), refusal=None, raw=raw)
