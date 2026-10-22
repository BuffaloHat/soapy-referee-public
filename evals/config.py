"""The settings of one run, loaded from YAML.

Every variable that could change an answer lives here, never in source: the
model, how it's sampled, which prompt it was given, and how much schema it saw.
A verdict is only meaningful next to the settings that produced it, so the run
record carries this whole object as its header.

Endpoints and secrets are not run variables — they come from the environment.
Two machines pointing at different Ollama hosts are running the same run.
"""

import os
from dataclasses import asdict, dataclass
from pathlib import Path

import yaml

CONFIGS_DIR = Path(__file__).resolve().parent / "configs"

# How much schema the agent is shown. The contrast is the point: it makes
# "does documentation help?" a measurable run setting rather than an opinion.
SCHEMA_MODES = {"columns", "docs"}

# What changes between attempts of the same case. At temperature 0 an identical
# prompt returns an identical answer, so without a perturbation `repeats`
# replays one result k times. `table_order` shuffles the order of tables in the
# schema block — same facts, different prompt — which is the kind of change
# that flipped an unrelated verdict on 2026-09-30 (docs/findings.md).
PERTURBATIONS = {"none", "table_order"}

# Who serves the model. Ollama for the local agent, OpenAI for the comparison
# arm; `agent/llm.py` routes each call by this.
PROVIDERS = {"ollama", "openai"}

REQUIRED = {"name", "model", "prompt_version", "schema_mode"}


class ConfigError(Exception):
    """A run config is malformed. Loud at load time, not mid-run."""


@dataclass(frozen=True)
class RunConfig:
    name: str
    model: str
    prompt_version: str
    schema_mode: str
    provider: str = "ollama"
    temperature: float = 0.0
    # Ollama honours a seed; with temperature 0 it mostly won't matter, but a
    # run that can't be repeated isn't evidence.
    seed: int = 0
    # Same case, several attempts. Only meaningful if something differs between
    # them — a perturbation, or temperature above 0.
    repeats: int = 1
    perturbation: str = "none"
    # Generation cap. Distinct from the DB layer's row cap and timeout.
    max_output_tokens: int = 512
    # Ollama's context window. It defaults to a few thousand tokens and drops
    # whatever overflows without erroring, so a schema block that outgrows it
    # produces a confidently wrong answer and no sign of why. Set it explicitly.
    # Ignored by OpenAI, whose window is fixed per model and refuses overflow.
    num_ctx: int = 32768
    # When the agent's SQL fails, show the model the database's error and let
    # it try again, up to this many times. 0 is the plain one-shot agent.
    sql_retries: int = 0
    # After the SQL runs, a second call writes a one- or two-sentence answer
    # from the rows. Off by default; the Phase 6 judge grades it.
    write_answer: bool = False
    notes: str = ""

    @property
    def ollama_host(self) -> str:
        """Where to reach the model. Environment, not config — see module docstring."""
        return os.environ.get("OLLAMA_HOST", "http://localhost:11434")

    def to_header(self) -> dict:
        """The run record's header. Only the settings, not the environment."""
        return asdict(self)


def _validate(raw: dict, source: str) -> None:
    missing = REQUIRED - raw.keys()
    if missing:
        raise ConfigError(f"{source}: missing {', '.join(sorted(missing))}")
    unknown = raw.keys() - {f.name for f in RunConfig.__dataclass_fields__.values()}
    if unknown:
        raise ConfigError(f"{source}: unknown field(s) {', '.join(sorted(unknown))}")
    if raw.get("provider", "ollama") not in PROVIDERS:
        raise ConfigError(f"{source}: provider must be one of {sorted(PROVIDERS)}")
    if raw["schema_mode"] not in SCHEMA_MODES:
        raise ConfigError(f"{source}: schema_mode must be one of {sorted(SCHEMA_MODES)}")
    if not 0.0 <= float(raw.get("temperature", 0.0)) <= 2.0:
        raise ConfigError(f"{source}: temperature must be between 0 and 2")
    if int(raw.get("repeats", 1)) < 1:
        raise ConfigError(f"{source}: repeats must be at least 1")
    perturbation = raw.get("perturbation", "none")
    if perturbation not in PERTURBATIONS:
        raise ConfigError(f"{source}: perturbation must be one of {sorted(PERTURBATIONS)}")
    # Greedy decoding plus an identical prompt gives k copies of one answer,
    # which would read as stability. Check determinism by running twice instead.
    if (int(raw.get("repeats", 1)) > 1 and perturbation == "none"
            and float(raw.get("temperature", 0.0)) == 0.0):
        raise ConfigError(
            f"{source}: repeats above 1 at temperature 0 needs a perturbation — "
            "otherwise every attempt replays the first"
        )
    if int(raw.get("max_output_tokens", 512)) < 1:
        raise ConfigError(f"{source}: max_output_tokens must be at least 1")
    if int(raw.get("sql_retries", 0)) < 0:
        raise ConfigError(f"{source}: sql_retries must be 0 or more")
    if int(raw.get("num_ctx", 32768)) < 2048:
        raise ConfigError(f"{source}: num_ctx must be at least 2048")


def from_dict(raw: dict, source: str = "<dict>") -> RunConfig:
    _validate(raw, source)
    return RunConfig(**raw)


def load(name_or_path: str) -> RunConfig:
    """Load a config by name (`evals/configs/<name>.yaml`) or by path."""
    path = Path(name_or_path)
    if not path.suffix:
        path = CONFIGS_DIR / f"{name_or_path}.yaml"
    if not path.exists():
        raise ConfigError(f"no config at {path}")

    raw = yaml.safe_load(path.read_text())
    if not isinstance(raw, dict):
        raise ConfigError(f"{path.name}: expected a mapping at the top level")
    return from_dict(raw, path.name)


def available() -> list[str]:
    """Config names on disk, for `--config` help and error messages."""
    return sorted(p.stem for p in CONFIGS_DIR.glob("*.yaml"))
