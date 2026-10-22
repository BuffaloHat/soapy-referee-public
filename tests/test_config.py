"""A malformed run config must fail at load time, not halfway through a run."""

import pytest

from evals import config


def base(**overrides) -> dict:
    raw = {
        "name": "t",
        "model": "qwen2.5-coder:14b",
        "prompt_version": "v1",
        "schema_mode": "columns",
    }
    raw.update(overrides)
    return raw


def test_minimal_config_loads_with_defaults():
    cfg = config.from_dict(base())
    assert cfg.temperature == 0.0
    assert cfg.repeats == 1
    assert cfg.seed == 0


def test_missing_required_field_is_rejected():
    raw = base()
    del raw["model"]
    with pytest.raises(config.ConfigError, match="model"):
        config.from_dict(raw)


def test_unknown_field_is_rejected():
    # A typo'd setting would otherwise be silently ignored, and the run
    # record would claim settings that were never applied.
    with pytest.raises(config.ConfigError, match="temprature"):
        config.from_dict(base(temprature=0.7))


def test_retries_are_off_unless_asked_for():
    assert config.from_dict(base()).sql_retries == 0
    with pytest.raises(config.ConfigError, match="sql_retries"):
        config.from_dict(base(sql_retries=-1))


def test_answers_are_off_unless_asked_for():
    assert config.from_dict(base()).write_answer is False


def test_bad_schema_mode_is_rejected():
    with pytest.raises(config.ConfigError, match="schema_mode"):
        config.from_dict(base(schema_mode="everything"))


@pytest.mark.parametrize("temperature", [-0.1, 2.1])
def test_temperature_out_of_range_is_rejected(temperature):
    with pytest.raises(config.ConfigError, match="temperature"):
        config.from_dict(base(temperature=temperature))


def test_repeats_below_one_is_rejected():
    with pytest.raises(config.ConfigError, match="repeats"):
        config.from_dict(base(repeats=0))


def test_max_output_tokens_below_one_is_rejected():
    with pytest.raises(config.ConfigError, match="max_output_tokens"):
        config.from_dict(base(max_output_tokens=0))


def test_missing_file_is_rejected():
    with pytest.raises(config.ConfigError, match="no config at"):
        config.load("does-not-exist")


def test_ollama_host_comes_from_the_environment(monkeypatch):
    cfg = config.from_dict(base())
    monkeypatch.setenv("OLLAMA_HOST", "http://elsewhere:11434")
    assert cfg.ollama_host == "http://elsewhere:11434"
    # And it stays out of the run record: it is not a run variable.
    assert "ollama_host" not in cfg.to_header()


def test_header_round_trips_through_from_dict():
    # The run record header has to be enough to rebuild the run.
    cfg = config.from_dict(base(temperature=0.3, repeats=3, notes="x"))
    assert config.from_dict(cfg.to_header()) == cfg


@pytest.mark.parametrize("name", config.available())
def test_shipped_configs_load(name):
    cfg = config.load(name)
    assert cfg.name == name


def test_the_two_arms_differ_only_by_schema_mode():
    # The comparison is only attributable if nothing else moved.
    columns = config.load("local-columns").to_header()
    docs = config.load("local-docs").to_header()
    differing = {k for k in columns if columns[k] != docs[k]}
    assert differing == {"name", "schema_mode", "notes"}


def test_unknown_perturbation_is_rejected():
    with pytest.raises(config.ConfigError, match="perturbation"):
        config.from_dict(base(repeats=3, perturbation="paraphrase"))


def test_repeats_at_temperature_zero_need_a_perturbation():
    # Greedy decoding over an identical prompt replays one answer k times,
    # which would read as stability without measuring any.
    with pytest.raises(config.ConfigError, match="needs a perturbation"):
        config.from_dict(base(repeats=3))


@pytest.mark.parametrize("overrides", [
    {"repeats": 3, "perturbation": "table_order"},
    {"repeats": 3, "temperature": 0.7},
    {"repeats": 1},
])
def test_repeats_that_can_differ_are_allowed(overrides):
    config.from_dict(base(**overrides))


@pytest.mark.parametrize("arm", ["local-columns", "local-docs"])
def test_each_noise_floor_arm_differs_from_its_base_only_in_attempts(arm):
    # Attempt 1 of the r3 arm must be the same prompt as the base arm.
    single = config.load(arm).to_header()
    repeated = config.load(f"{arm}-r3").to_header()
    differing = {k for k in single if single[k] != repeated[k]}
    assert differing == {"name", "repeats", "perturbation", "notes"}


def test_the_provider_defaults_to_ollama():
    # Every config written before the OpenAI arm keeps meaning what it meant.
    assert config.from_dict(base()).provider == "ollama"


def test_an_unknown_provider_is_rejected():
    with pytest.raises(config.ConfigError, match="provider"):
        config.from_dict(base(provider="anthropic"))
