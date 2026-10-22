"""The faithfulness judge: does the agent's prose say only what its rows say?

An LLM grades what has no exact right answer. It sees the question, the rows
the answer was written from (the row preview in the run record) and the
answer — never the gold answer, because it grades faithfulness, not
correctness (overview.md, principle 6). Correctness stays with the comparator.

A judge is a model, so it is wrong sometimes. Its verdicts only count once
they have been compared with a person's labels on the same items: how often
they agree, and which way the judge errs.

    uv run python -m evals.judge --run <run_id>
    uv run python -m evals.judge --run <run_id> --send

Reads the run's rows that have an answer, plus `judge_set.jsonl` if the run
folder has one (the human-labelled set, which may include planted answers).
Writes `judgements.jsonl` next to them and prints agreement on the labelled
items. `--send` puts the judge's verdict and the human label in Langfuse as
`faithful_judge` and `faithful_human` on each trace's root step.
"""

import argparse
import base64
import json
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path

import yaml
from dotenv import load_dotenv

from agent import ollama
from evals import record, scores

ROOT = Path(__file__).resolve().parent
PROMPTS_DIR = ROOT / "judge_prompts" / "faithfulness"
CONFIGS_DIR = ROOT / "configs" / "judge"
SET_FILE = "judge_set.jsonl"
OUT_FILE = "judgements.jsonl"


class JudgeError(Exception):
    """The judge's config or prompt is missing."""


@dataclass(frozen=True)
class JudgeConfig:
    """The judge's own run settings — config, like the agent's, never source."""

    name: str
    model: str
    prompt_version: str
    temperature: float = 0.0
    seed: int = 0
    num_ctx: int = 8192
    max_output_tokens: int = 4096

    @property
    def ollama_host(self) -> str:
        return os.environ.get("OLLAMA_HOST", "http://localhost:11434")


@dataclass(frozen=True)
class Judgement:
    faithful: bool | None      # None: the reply could not be read
    reason: str
    raw: str


def load_config(name: str) -> JudgeConfig:
    path = CONFIGS_DIR / f"{name}.yaml"
    if not path.exists():
        raise JudgeError(f"no judge config at {path}")
    return JudgeConfig(**yaml.safe_load(path.read_text()))


def build(cfg: JudgeConfig, question: str, rows_text: str, answer: str) -> tuple[str, str]:
    """System and user text. Only what the answer-writer saw, plus the answer."""
    path = PROMPTS_DIR / f"{cfg.prompt_version}.md"
    if not path.exists():
        raise JudgeError(f"no judge prompt '{cfg.prompt_version}' in {PROMPTS_DIR}")
    user = (f"Question: {question.strip()}\n\nQuery result:\n{rows_text}\n\n"
            f"Answer: {answer.strip()}")
    return path.read_text().strip(), user


def parse(text: str) -> Judgement:
    """The first non-empty line decides; the next one is the reason."""
    lines = [line.strip() for line in text.strip().splitlines() if line.strip()]
    if not lines:
        return Judgement(None, "", text)
    # Models echo the prompt's own wording ("Line 1: UNFAITHFUL"); drop it.
    head = re.sub(r"^line\s*1\s*:", "", lines[0].strip("*#: "), flags=re.IGNORECASE)
    head = head.strip("*#: ").upper()
    reason = re.sub(r"^line\s*2\s*:\s*", "", lines[1], flags=re.IGNORECASE) if len(lines) > 1 else ""
    # UNFAITHFUL contains FAITHFUL, so it is checked first.
    if head.startswith("UNFAITHFUL"):
        return Judgement(False, reason, text)
    if head.startswith("FAITHFUL"):
        return Judgement(True, reason, text)
    return Judgement(None, reason, text)


def judge(cfg: JudgeConfig, question: str, rows_text: str, answer: str) -> Judgement:
    system, user = build(cfg, question, rows_text, answer)
    return parse(ollama.generate(cfg, user, system=system).text)


def agreement(pairs: list[tuple[bool, bool | None]]) -> dict:
    """Judge against human, as (human, judge) pairs of faithful-or-not.

    `lenient` is the judge passing what the human failed — the costly error,
    since an unfaithful answer gets through. `strict` is the reverse.
    """
    scored = [(h, j) for h, j in pairs if j is not None]
    return {
        "items": len(pairs),
        "agree": sum(h == j for h, j in scored),
        "lenient": sum(not h and j for h, j in scored),
        "strict": sum(h and not j for h, j in scored),
        "unreadable": len(pairs) - len(scored),
    }


def items_for(directory: Path) -> list[dict]:
    """Every answer to judge: the labelled set first, then the run's other answers."""
    _, rows = record.load(directory)
    labelled = []
    set_path = directory / SET_FILE
    if set_path.exists():
        labelled = [json.loads(line) for line in set_path.read_text().splitlines() if line.strip()]
    seen = {(i["case_id"], i["attempt"]) for i in labelled if i["source"] == "agent"}
    rest = [
        {"id": f"{r['case_id']}#{r['attempt']}", "case_id": r["case_id"], "attempt": r["attempt"],
         "trace_id": r["trace_id"], "span_id": r["span_id"], "question": r["question"],
         "rows_text": record.preview_text(r["agent_preview"]), "answer": r["answer"],
         "source": "agent"}
        for r in rows
        if r.get("answer") and (r["case_id"], r["attempt"]) not in seen
    ]
    return labelled + rest


def send_to_fuse(results: list[dict], call=scores.http) -> int:
    """Judge and human verdicts as boolean scores on each agent answer's root step."""
    host = os.environ["LANGFUSE_HOST"].rstrip("/")
    keys = f"{os.environ['LANGFUSE_PUBLIC_KEY']}:{os.environ['LANGFUSE_SECRET_KEY']}"
    headers = {"Authorization": f"Basic {base64.b64encode(keys.encode()).decode()}"}
    sent = 0
    for item in results:
        if item["source"] != "agent" or not item.get("trace_id"):
            continue       # planted answers have no trace of their own
        grades = [("faithful_judge", item["judge"], item["judge_reason"])]
        if item.get("human_final"):
            grades.append(("faithful_human", item["human_final"] == "F", item.get("human_reason", "")))
        for name, value, comment in grades:
            if value is None:
                continue
            status, _ = call("POST", f"{host}/api/public/scores", headers, {
                "id": f"{item['trace_id']}-{name}", "traceId": item["trace_id"],
                "observationId": item["span_id"], "name": name, "dataType": "BOOLEAN",
                "value": 1 if value else 0, "comment": comment or None, "environment": "eval",
            })
            sent += 200 <= status < 300
    return sent


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Judge a run's prose answers for faithfulness.")
    parser.add_argument("--run", required=True, help="run id or directory")
    parser.add_argument("--config", default="faithfulness-gemma")
    parser.add_argument("--runs-dir", type=Path, default=None)
    parser.add_argument("--send", action="store_true", help="also send verdicts to Langfuse")
    args = parser.parse_args(argv)

    load_dotenv(ROOT.parent / ".env")
    cfg = load_config(args.config)
    directory = Path(args.run) if Path(args.run).is_dir() else \
        Path(args.runs_dir or record.DEFAULT_RUNS_DIR) / args.run

    results = []
    for item in items_for(directory):
        verdict = judge(cfg, item["question"], item["rows_text"], item["answer"])
        results.append(item | {"judge": verdict.faithful, "judge_reason": verdict.reason,
                               "judge_raw": verdict.raw, "judge_config": asdict(cfg)})
        mark = {True: "F", False: "U", None: "?"}[verdict.faithful]
        human = item.get("human_final", "-")
        print(f"  {item['id']:<40} human {human}  judge {mark}  {verdict.reason[:70]}")

    (directory / OUT_FILE).write_text("".join(json.dumps(r) + "\n" for r in results))
    labelled = [(r["human_final"] == "F", r["judge"]) for r in results if r.get("human_final")]
    if labelled:
        a = agreement(labelled)
        print(f"\nagreement on {a['items']} labelled: {a['agree']} agree · judge too lenient "
              f"{a['lenient']} · too strict {a['strict']} · unreadable {a['unreadable']}")
    if args.send:
        print(f"sent {send_to_fuse(results)} scores to Langfuse")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
