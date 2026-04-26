"""Placeholder publishers — keeps the SCHEMA SELECT honest until impl lands."""

from __future__ import annotations

from pathlib import Path

from . import Publisher


class StubPublisher(Publisher):
    def __init__(self, name: str) -> None:
        self.name = name

    def publish(self, local_dir: Path) -> str:
        raise NotImplementedError(
            f"publisher {self.name!r} not implemented yet — see docs/plans/drafts/020"
        )
