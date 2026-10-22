"""One model call, routed to the provider the run config names.

The runner calls this and never a provider module directly, so switching the
agent from qwen to an OpenAI model is a config change, not a code change.
"""

from agent import ollama, openai_api
from agent.completion import Completion, ContextOverflowError, LLMError  # noqa: F401

PROVIDERS = {"ollama": ollama, "openai": openai_api}


def _provider(config):
    try:
        return PROVIDERS[config.provider]
    except KeyError:
        raise LLMError(f"unknown provider {config.provider!r}") from None


def generate(config, prompt: str, system: str | None = None) -> Completion:
    return _provider(config).generate(config, prompt, system=system)


def preflight(config, prompt: str, system: str | None = None) -> Completion:
    return _provider(config).preflight(config, prompt, system=system)
