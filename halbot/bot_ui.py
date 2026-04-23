"""Embed + component helpers for Halbot's Discord surface.

Implements the visual grammar defined in docs/mockups/discord_interactions/:
italic subtext lead-in → colored embed → optional ActionRow / SelectMenu.

See docs/plans/014-discord-embed-flows-impl.md for the rollout plan.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable, Sequence

import discord

log = logging.getLogger("halbot.ui")


class Mode(str, Enum):
    SOUNDBOARD = "soundboard"
    SAVED = "saved"
    TRIGGER = "trigger"
    ADMIN_STATUS = "admin/status"
    ADMIN_DELETED = "admin/deleted"
    ADMIN_UNDELETE = "admin/undelete"
    ADMIN_PANIC = "admin/panic"
    PANIC_COMPLETE = "panic complete"
    PERSONA_SAVED = "persona saved"
    PERSONA_ACTIVE = "active persona"
    NOTED = "noted"
    GRUDGE_LEDGER = "grudge ledger"
    REFUSED = "persona declined"
    DENIED = "permission denied"
    WAKE = "wake"
    VOICE_TRIGGER = "voice trigger"
    ACTIONED = "actioned"
    ERROR = "error"


COLORS: dict[str, int] = {
    "amber":  0xE8B15C,
    "good":   0x7CCFA0,
    "warn":   0xE8A361,
    "bad":    0xD55D48,
    "violet": 0xB88AD0,
    "cyan":   0x7EC3D8,
}


MODE_COLOR: dict[Mode, str] = {
    Mode.SOUNDBOARD: "amber",
    Mode.SAVED: "good",
    Mode.TRIGGER: "violet",
    Mode.ADMIN_STATUS: "cyan",
    Mode.ADMIN_DELETED: "warn",
    Mode.ADMIN_UNDELETE: "good",
    Mode.ADMIN_PANIC: "bad",
    Mode.PANIC_COMPLETE: "bad",
    Mode.PERSONA_SAVED: "violet",
    Mode.PERSONA_ACTIVE: "amber",
    Mode.NOTED: "cyan",
    Mode.GRUDGE_LEDGER: "warn",
    Mode.REFUSED: "warn",
    Mode.DENIED: "bad",
    Mode.WAKE: "amber",
    Mode.VOICE_TRIGGER: "violet",
    Mode.ACTIONED: "good",
    Mode.ERROR: "bad",
}


HALBOT_AUTHOR_ICON = "https://cdn.discordapp.com/embed/avatars/0.png"
"""Default author-icon. Overridden by config.halbot_avatar_url when set."""


@dataclass
class EmbedField:
    name: str
    value: str
    inline: bool = True


@dataclass
class ReplyPayload:
    """Structured reply spec — maps 1:1 to a Discord message."""
    mode: Mode
    title: str
    description: str | None = None
    subtext: str | None = None
    fields: Sequence[EmbedField] = field(default_factory=tuple)
    footer: str | None = None
    color: str | None = None  # override MODE_COLOR


def _resolve_color(mode: Mode, override: str | None) -> int:
    key = override or MODE_COLOR.get(mode, "amber")
    return COLORS.get(key, COLORS["amber"])


def _resolve_avatar_url() -> str:
    try:
        from . import config as _config
        url = (_config.get("halbot_avatar_url") or "").strip()
        return url or HALBOT_AUTHOR_ICON
    except Exception:
        return HALBOT_AUTHOR_ICON


def build_embed(payload: ReplyPayload) -> discord.Embed:
    emb = discord.Embed(
        title=payload.title,
        color=_resolve_color(payload.mode, payload.color),
    )
    if payload.description:
        emb.description = payload.description
    emb.set_author(name=f"Halbot · {payload.mode.value}", icon_url=_resolve_avatar_url())
    for f in payload.fields:
        emb.add_field(name=f.name, value=f.value, inline=f.inline)
    if payload.footer:
        emb.set_footer(text=payload.footer)
    return emb


async def send_halbot_reply(
    dest: "discord.abc.Messageable | discord.Message",
    *,
    payload: ReplyPayload,
    view: discord.ui.View | None = None,
    reply_to: discord.Message | None = None,
) -> discord.Message:
    """Send a structured Halbot reply to a channel or as a message reply.

    Composes: italic subtext line (outside embed) + embed + optional view.
    Subtext rides as a `content` prefix so it renders above the embed in
    Discord's UI. If ``reply_to`` is passed, uses ``message.reply`` to
    produce a proper inline reply.
    """
    content = f"-# *{payload.subtext}*" if payload.subtext else None
    embed = build_embed(payload)
    send_kwargs: dict = {"embed": embed}
    if content:
        send_kwargs["content"] = content
    if view is not None:
        send_kwargs["view"] = view

    if reply_to is not None:
        return await reply_to.reply(**send_kwargs, mention_author=False)
    if isinstance(dest, discord.Message):
        return await dest.reply(**send_kwargs, mention_author=False)
    return await dest.send(**send_kwargs)


def fenced_table(rows: Iterable[Sequence[str]], *, headers: Sequence[str] | None = None) -> str:
    """Render a fixed-width table inside a ```fenced``` code block.

    Used for admin/status, tombstone listings, etc. Discord renders
    monospace reliably inside fenced blocks — embed fields mangle spacing.
    """
    rows = [list(r) for r in rows]
    if headers:
        header_row = list(headers)
        widths = [len(h) for h in header_row]
    else:
        header_row = None
        widths = [0] * (len(rows[0]) if rows else 0)
    for r in rows:
        for i, cell in enumerate(r):
            if i >= len(widths):
                widths.append(len(cell))
            else:
                widths[i] = max(widths[i], len(cell))

    def fmt(cells: Sequence[str]) -> str:
        return " ".join(c.ljust(widths[i]) for i, c in enumerate(cells))

    lines: list[str] = []
    if header_row:
        lines.append(fmt(header_row))
        lines.append(" ".join("─" * w for w in widths))
    for r in rows:
        lines.append(fmt(r))
    return "```\n" + "\n".join(lines) + "\n```"


def refusal_payload(reason: str) -> ReplyPayload:
    """Standard persona-refusal embed."""
    return ReplyPayload(
        mode=Mode.REFUSED,
        title="Not happening.",
        description=reason,
        subtext="Persona declined · no side-effects",
        footer="This is Halbot's active persona talking — tune it in the dashboard.",
    )


def denied_payload(reason: str = "This is owner-only.") -> ReplyPayload:
    return ReplyPayload(
        mode=Mode.DENIED,
        title="Permission denied",
        description=reason,
        subtext="Caller not guild owner",
    )
