"""The eval report: hand-written narrative, generated numbers.

The report's prose lives in a Markdown template; every table of numbers in
it comes from the run record through `evals/metrics.py`, filled in at
`{{placeholders}}`. Re-running this after a new run updates every number and
leaves the argument to be re-read, never a figure to be re-typed.

    uv run python -m evals.report            # writes docs/sref_eval_report.md
    tools/build_pdf.sh docs/sref_eval_report.md docs/sref_eval_report.pdf

Placeholders: {{arms}} {{metrics}} {{grid}} {{taxonomy}} {{judge}} {{generated}}.
An unknown or unfilled placeholder stops the build.
"""

import argparse
import json
import re
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import yaml

from evals import metrics, record
from evals.config import CONFIGS_DIR
from evals.judge import agreement

ROOT = Path(__file__).resolve().parent.parent
CONFIG = CONFIGS_DIR / "report" / "default.yaml"
PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")
TIER_ORDER = ["baseline", "trap", "hard", "refusal"]
SHORT = {"refusal_correct": "declined", "refusal_absent": "answered",
         "refusal_unexpected": "declined"}


class ReportError(Exception):
    """The report can't be built: a missing run or a bad placeholder."""


def load_run(runs_dir: Path, run_id: str) -> tuple[dict, list[dict]]:
    directory = runs_dir / run_id
    if not (directory / record.HEADER_FILE).exists():
        raise ReportError(f"no run at {directory}")
    return record.load(directory)


def table(header: list[str], rows: list[list]) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]
    return "\n".join(lines)


def share(part: int, whole: int) -> str:
    return f"{part}/{whole} ({100 * part / whole:.0f}%)" if whole else "—"


def arms_table(arms: list[dict]) -> str:
    rows = []
    for arm in arms:
        cfg = arm["header"]["config"]
        rows.append([arm["label"], f"`{cfg['model']}`", cfg.get("provider", "ollama"),
                     cfg["temperature"], cfg["schema_mode"], cfg["prompt_version"],
                     cfg["repeats"], f"`{arm['header']['run_id']}`"])
    return table(["Arm", "Model", "Provider", "Temperature", "Schema", "Prompt",
                  "Attempts", "Run"], rows)


def metrics_table(arms: list[dict]) -> str:
    s = [arm["summary"] for arm in arms]

    def cost(x):
        return "$0 (local)" if x["cost"] is None else f"${x['cost']:.2f}"

    rows = [
        ["**Pass rate**", *(share(x["pass"], x["attempts"]) for x in s)],
        ["Pass rate, lenient", *(share(x["lenient"], x["attempts"]) for x in s)],
        ["**Silent-failure rate**", *(share(x["silent"], x["attempts"]) for x in s)],
        ["Silent share of failures", *(f"{x['silent']} of {x['failures']}" for x in s)],
        ["Loud-failure rate", *(share(x["loud"], x["attempts"]) for x in s)],
        ["pass^k (every attempt passes)", *(f"{x['pass_all']}/{x['cases']} cases" for x in s)],
        ["pass@k (any attempt passes)", *(f"{x['pass_any']}/{x['cases']} cases" for x in s)],
        ["Verdict changed / crossed pass line",
         *(f"{x['verdict_changed']} / {x['pass_changed']} cases" for x in s)],
        ["Median model time per attempt", *(f"{x['median_model_s']:.1f} s" for x in s)],
        ["Cost of the run", *(cost(x) for x in s)],
    ]
    return table(["Metric", *(arm["label"] for arm in arms)], rows)


def grid_table(arms: list[dict], tiers: dict[str, str]) -> str:
    by_arm = []
    for arm in arms:
        cells: dict[str, list[str]] = {}
        for row in sorted(arm["rows"], key=lambda r: r["attempt"]):
            cells.setdefault(row["case_id"], []).append(row["verdict"])
        by_arm.append(cells)
    cases = sorted(tiers, key=lambda c: (TIER_ORDER.index(tiers[c]), c))
    rows = []
    for case in cases:
        row = [tiers[case], f"`{case}`"]
        for cells in by_arm:
            verdicts = cells.get(case, [])
            passed = sum(v in metrics.PASSING for v in verdicts)
            row.append(f"{' · '.join(SHORT.get(v, v) for v in verdicts)} "
                       f"(**{passed}/{len(verdicts)}**)")
        rows.append(row)
    return table(["Tier", "Case", *(arm["label"] for arm in arms)], rows)


def taxonomy_table(rows: list[dict]) -> str:
    counts = Counter((r["failure_type"], r["failure_type_name"]) for r in rows)
    example = {}
    for r in rows:
        example.setdefault(r["failure_type"], r["note"])
    body = [[name, n, example[code]] for (code, name), n in sorted(counts.items())]
    body.append(["**All failures**", sum(counts.values()), ""])
    return table(["Failure type", "Attempts", "Example"], body)


def judge_table(items: list[dict]) -> str:
    labelled = [i for i in items if i.get("human_final")]
    result = agreement([(i["human_final"] == "F", i.get("judge")) for i in labelled])
    planted = sum(i.get("source") != "agent" for i in labelled)
    return table(["Measure", "Count"], [
        ["Answers labelled by a person (planted unfaithful)",
         f"{result['items']} ({planted})"],
        ["Judge agrees with the person", f"{result['agree']} of {result['items']}"],
        ["Lenient miss: passed an unfaithful answer", result["lenient"]],
        ["Strict miss: failed a faithful answer", result["strict"]],
        ["Unreadable verdicts", result["unreadable"]],
    ])


def build(config: dict, runs_dir: Path, tiers: dict[str, str]) -> dict[str, str]:
    """Every placeholder's text."""
    arms = []
    for arm in config["arms"]:
        header, rows = load_run(runs_dir, arm["run"])
        arms.append({"label": arm["label"], "header": header, "rows": rows,
                     "summary": metrics.summarize(rows, arm.get("price"))})
    load_run(runs_dir, config["taxonomy_run"])          # the run must exist
    taxonomy_path = runs_dir / config["taxonomy_run"] / "taxonomy.jsonl"
    judge_path = runs_dir / config["judge_run"] / "judgements.jsonl"
    for path in (taxonomy_path, judge_path):
        if not path.exists():
            raise ReportError(f"missing {path}")
    taxonomy = [json.loads(line) for line in taxonomy_path.read_text().splitlines() if line]
    judgements = [json.loads(line) for line in judge_path.read_text().splitlines() if line]
    runs = ", ".join(f"<code>{a['header']['run_id']}</code>" for a in arms)
    return {
        "arms": arms_table(arms),
        "metrics": metrics_table(arms),
        "grid": grid_table(arms, tiers),
        "taxonomy": taxonomy_table(taxonomy),
        "judge": judge_table(judgements),
        # Lands inside an HTML paragraph, where Markdown backticks don't render.
        "generated": (f"Tables generated by <code>evals/report.py</code> on "
                      f"{datetime.now(UTC).date().isoformat()} from runs {runs}, "
                      f"<code>{config['judge_run']}</code> and "
                      f"<code>{config['taxonomy_run']}</code>, at commit "
                      f"<code>{record.git_commit()}</code>."),
    }


def fill(template: str, values: dict[str, str]) -> str:
    used = set(PLACEHOLDER.findall(template))
    unknown = used - values.keys()
    if unknown:
        raise ReportError(f"template has unknown placeholder(s): {sorted(unknown)}")
    return PLACEHOLDER.sub(lambda m: values[m.group(1)], template)


def main(argv=None) -> int:
    from evals import cases as case_loader

    parser = argparse.ArgumentParser(description="Write the eval report from the run record.")
    parser.add_argument("--runs-dir", type=Path, default=None)
    args = parser.parse_args(argv)

    config = yaml.safe_load(CONFIG.read_text())
    runs_dir = Path(args.runs_dir or record.DEFAULT_RUNS_DIR)
    tiers = {c.id: c.tier for c in case_loader.load_cases()}
    try:
        values = build(config, runs_dir, tiers)
        text = fill((ROOT / config["template"]).read_text(), values)
    except ReportError as error:
        print(f"report: {error}")
        return 1
    out = ROOT / config["output"]
    out.write_text(text)
    print(f"wrote {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
