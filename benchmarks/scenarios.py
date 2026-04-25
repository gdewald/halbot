"""Built-in scenarios.

Keeps benchmark configs in-repo so results are reproducible and diff-able.
The baseline mirrors the current production voice path; comparison
scenarios perturb one axis at a time (model size, beam width, quant,
voice, etc.).

Stub only. Real scenario definitions in 016-voice-pipeline-benchmarks.
"""
from __future__ import annotations

from .runner import Scenario


def baseline() -> Scenario:
    """Current production config: whisper large-v3-turbo fp16 + gemma3 +
    kokoro af_heart. Upper bound on what real users experience."""
    raise NotImplementedError("baseline scenario — see plan 016")


def all_scenarios() -> list[Scenario]:
    raise NotImplementedError("scenario registry — see plan 016")
