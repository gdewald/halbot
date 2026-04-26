"""Backends that take a local directory and publish it under a public URL.

Selected at runtime by ``stats_publisher`` config; called by
``halbot.stats_publisher.publish_now``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path


class Publisher(ABC):
    """Push the contents of ``local_dir`` and return the resulting public URL."""

    @abstractmethod
    def publish(self, local_dir: Path) -> str: ...


def get_publisher(name: str) -> Publisher:
    name = (name or "").strip().lower() or "s3"
    if name == "s3":
        from .s3 import S3Publisher
        return S3Publisher()
    if name in ("filesystem", "github_pages"):
        from ._stubs import StubPublisher
        return StubPublisher(name)
    raise ValueError(f"unknown publisher {name!r}")
