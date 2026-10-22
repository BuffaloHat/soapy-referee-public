"""The regression gate: has a run slipped below its baseline?

A change to the agent (a prompt, a model, a schema setting) is only safe if
it doesn't lose ground it already held. The gate compares a new run with a
pinned baseline run, from the run record alone, and fails on either rule:

1. **Score line.** The pass rate over all attempts must not fall more than
   `margin_points` below the baseline's. The margin is the noise allowance:
   with repeats, the same setup crosses the pass line on some cases by chance.
2. **No lost stable case.** A case that passed on every baseline attempt must
   not fail on every attempt now. 3/3 to 2/3 can be noise; 3/3 to 0/3 isn't.

In SQL terms, a threshold test on the run plus a row-level test per case.

Two runs are comparable only on the same snapshot, the same cases and the same
number of attempts; otherwise the gate refuses to judge. The result is written
to `gate.json` in the run's folder.

    uv run python -m evals.gate                      # the latest run
    uv run python -m evals.gate --run <run_id> [--baseline <run_id>] [--gate default]

Exit code: 0 passed, 1 failed, 2 could not compare.
"""

import argparse
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

import yaml

from evals import record
from evals.config import CONFIGS_DIR
from evals.runner import PASSING

GATE_DIR = CONFIGS_DIR / "gate"
OUT_FILE = "gate.json"


class GateError(Exception):
    """The gate can't judge: no config, no baseline, or runs not comparable."""


@dataclass(frozen=True)
class GateConfig:
    name: str
    margin_points: float = 10.0
    baselines: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Check:
    name: str
    passed: bool
    detail: str
    cases: list = field(default_factory=list)


def load_gate(name: str) -> GateConfig:
    path = GATE_DIR / f"{name}.yaml"
    if not path.exists():
        raise GateError(f"no gate config at {path}")
    return GateConfig(**yaml.safe_load(path.read_text()))


def pass_counts(rows: list[dict]) -> dict[str, tuple[int, int]]:
    """Per case: (attempts passed, attempts run)."""
    counts: dict[str, tuple[int, int]] = {}
    for row in rows:
        passed, total = counts.get(row["case_id"], (0, 0))
        counts[row["case_id"]] = (passed + (row["verdict"] in PASSING), total + 1)
    return counts


def pass_rate(rows: list[dict]) -> float:
    return 100.0 * sum(r["verdict"] in PASSING for r in rows) / len(rows) if rows else 0.0


def comparable(base: tuple[dict, list], new: tuple[dict, list]) -> None:
    """Raise unless the two runs measured the same thing."""
    (base_header, base_rows), (new_header, new_rows) = base, new
    if base_header.get("snapshot_sha256") != new_header.get("snapshot_sha256"):
        raise GateError("different snapshots: gold answers are not the same")
    base_counts, new_counts = pass_counts(base_rows), pass_counts(new_rows)
    if set(base_counts) != set(new_counts):
        raise GateError(f"different cases: {sorted(set(base_counts) ^ set(new_counts))}")
    if {c[1] for c in base_counts.values()} != {c[1] for c in new_counts.values()}:
        raise GateError("different numbers of attempts per case")


def score_line(base_rows, new_rows, margin: float) -> Check:
    base, new = pass_rate(base_rows), pass_rate(new_rows)
    line = base - margin
    return Check("score line", new >= line,
                 f"pass rate {new:.1f}% against a line of {line:.1f}% "
                 f"(baseline {base:.1f}% minus {margin:g} points)")


def stable_cases(base_rows, new_rows) -> Check:
    base, new = pass_counts(base_rows), pass_counts(new_rows)
    stable = [c for c, (passed, total) in base.items() if passed == total]
    lost = sorted(c for c in stable if new[c][0] == 0)
    detail = (f"{len(stable)} case(s) passed every baseline attempt; "
              f"{len(lost)} now fail every attempt")
    return Check("no lost stable case", not lost, detail, lost)


def config_changes(base_header: dict, new_header: dict) -> dict:
    """Settings that differ — the change under test, shown for the record."""
    base, new = base_header.get("config", {}), new_header.get("config", {})
    return {k: [base.get(k), new.get(k)] for k in sorted(base.keys() | new.keys())
            if k not in ("name", "notes") and base.get(k) != new.get(k)}


def evaluate(base_dir: Path, new_dir: Path, gate: GateConfig) -> dict:
    base, new = record.load(base_dir), record.load(new_dir)
    comparable(base, new)
    checks = [score_line(base[1], new[1], gate.margin_points),
              stable_cases(base[1], new[1])]
    return {
        "run_id": new[0]["run_id"],
        "baseline": base[0]["run_id"],
        "gate": gate.name,
        "passed": all(c.passed for c in checks),
        "checks": [asdict(c) for c in checks],
        "config_changes": config_changes(base[0], new[0]),
    }


def baseline_for(header: dict, gate: GateConfig) -> str:
    name = header.get("config", {}).get("name")
    if name not in gate.baselines:
        raise GateError(f"gate '{gate.name}' has no baseline for config '{name}'; "
                        "pass --baseline or pin one in the gate config")
    return gate.baselines[name]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Fail if a run slipped below its baseline.")
    parser.add_argument("--run", help="run id or directory; default the latest run")
    parser.add_argument("--baseline", help="run id or directory; default from the gate config")
    parser.add_argument("--gate", default="default", help="gate config name")
    parser.add_argument("--runs-dir", type=Path, default=None)
    args = parser.parse_args(argv)

    runs_dir = Path(args.runs_dir or record.DEFAULT_RUNS_DIR)

    def find(run: str) -> Path:
        directory = Path(run) if Path(run).is_dir() else runs_dir / run
        if not (directory / record.HEADER_FILE).exists():
            raise GateError(f"no run at {directory}")
        return directory

    try:
        gate = load_gate(args.gate)
        new_dir = find(args.run) if args.run else record.latest(runs_dir)
        if new_dir is None:
            raise GateError(f"no runs in {runs_dir}")
        new_header, _ = record.load(new_dir)
        base_dir = find(args.baseline or baseline_for(new_header, gate))
        if base_dir.resolve() == new_dir.resolve():
            raise GateError("the run is its own baseline")
        result = evaluate(base_dir, new_dir, gate)
    except GateError as error:
        print(f"gate: cannot compare — {error}")
        return 2

    (new_dir / OUT_FILE).write_text(json.dumps(result, indent=2) + "\n")
    print(f"gate {result['gate']}: {result['run_id']} against {result['baseline']}")
    for check in result["checks"]:
        print(f"  {'PASS' if check['passed'] else 'FAIL'}  {check['name']}: {check['detail']}")
        for case_id in check["cases"]:
            print(f"        {case_id}")
    for key, (before, after) in result["config_changes"].items():
        print(f"  changed  {key}: {before} -> {after}")
    print("PASSED" if result["passed"] else "FAILED")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
