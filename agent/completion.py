"""What one model call returns, and how it fails — the same for every provider.

The runner reads these and nothing provider-specific, so a run record from an
Ollama model and one from an OpenAI model have the same shape.
"""

from dataclasses import dataclass


class LLMError(Exception):
    """The model could not be called, or answered in a way we won't use."""


class ContextOverflowError(LLMError):
    """The prompt did not fit the context window. Never silently truncated."""


@dataclass(frozen=True)
class Completion:
    text: str
    model: str
    prompt_tokens: int
    output_tokens: int
    elapsed_s: float
    # The provider's reason for stopping. "length" means the output hit
    # max_output_tokens and the answer is cut off — the mirror of the input
    # guard, and a fact the run record needs.
    done_reason: str
    # Hidden reasoning, counted inside `output_tokens` and billed as output.
    # A reasoning model can spend the whole output cap here and return no text,
    # so a "length" stop with an empty answer needs this to be read.
    reasoning_tokens: int = 0
    # Part of `prompt_tokens`: input the provider served from its prompt cache,
    # billed at a discount. Ollama caches too but does not report it, so 0 there.
    cached_tokens: int = 0

    @property
    def output_truncated(self) -> bool:
        return self.done_reason == "length"
