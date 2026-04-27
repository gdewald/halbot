"""Slash command surface for Halbot.

Currently hosts the ``/halbot-admin`` group (flows 06/07/08 in the v3
mockup). Owner-only by runtime check — we also set
``default_member_permissions=0`` so Discord hides the command from
non-admin users by default, but the real gate is
``interaction.user.id == guild.owner_id``.

Registered on the client's ``discord.app_commands.CommandTree`` via
``register_slash(client)`` from ``bot.build_client``; tree sync runs in
``on_ready``.
"""
from __future__ import annotations

import asyncio
import logging

import discord
from discord import app_commands

from . import db
from . import stats_publisher
from . import voice as _voice
from .bot_ui import (
    EmbedField,
    Mode,
    ReplyPayload,
    build_embed,
    fenced_table,
)
from .interactions import (
    AdminStatusView,
    PanicConfirmView,
    UndeleteView,
)
from .llm import generate_wake_variants_async

log = logging.getLogger("halbot.slash")


def _is_owner(interaction: discord.Interaction) -> bool:
    guild = interaction.guild
    if not guild:
        return False
    return getattr(guild, "owner_id", None) == interaction.user.id


async def _deny(interaction: discord.Interaction) -> None:
    await interaction.response.send_message(
        "Owner-only.", ephemeral=True,
    )


def _kind_choices() -> list[app_commands.Choice[str]]:
    return [app_commands.Choice(name=k, value=k) for k in db.admin_kinds()]


admin_group = app_commands.Group(
    name="halbot-admin",
    description="Owner-only Halbot recovery / panic controls.",
    default_permissions=discord.Permissions(administrator=True),
    guild_only=True,
)


@admin_group.command(name="status", description="Live + tombstoned row counts per kind.")
async def admin_status(interaction: discord.Interaction) -> None:
    if not _is_owner(interaction):
        await _deny(interaction)
        return
    try:
        stats = db.admin_stats()
    except Exception:
        log.exception("[slash] status failed")
        await interaction.response.send_message("Status query failed.", ephemeral=True)
        return

    rows = [[k, str(v["live"]), str(v["deleted"])] for k, v in stats.items()]
    table = fenced_table(rows, headers=["kind", "live", "tomb"])
    total_tomb = sum(v["deleted"] for v in stats.values())
    emb = build_embed(ReplyPayload(
        mode=Mode.ADMIN_STATUS,
        title="Halbot admin · store status",
        description=table,
        subtext=f"admin/status · {total_tomb} recoverable row(s)",
        footer="Use the buttons below or run the matching slash subcommands.",
    ))
    await interaction.response.send_message(embed=emb, view=AdminStatusView())


@admin_group.command(name="deleted", description="List soft-deleted rows of one kind (newest first).")
@app_commands.describe(
    kind="Which table to browse.",
    limit="Max rows to list (default 25, max 200).",
)
@app_commands.choices(kind=_kind_choices())
async def admin_deleted(
    interaction: discord.Interaction,
    kind: app_commands.Choice[str],
    limit: int = 25,
) -> None:
    if not _is_owner(interaction):
        await _deny(interaction)
        return
    lim = max(1, min(200, int(limit)))
    try:
        rows = db.admin_list_deleted(kind.value, lim)
    except ValueError as e:
        await interaction.response.send_message(str(e), ephemeral=True)
        return
    except Exception:
        log.exception("[slash] deleted list failed")
        await interaction.response.send_message("Listing failed.", ephemeral=True)
        return

    if not rows:
        emb = build_embed(ReplyPayload(
            mode=Mode.ADMIN_DELETED,
            title=f"No tombstoned `{kind.value}`",
            subtext=f"admin/deleted · {kind.value}",
            footer="Empty tombstone — nothing to recover.",
        ))
        await interaction.response.send_message(embed=emb)
        return

    table_rows = []
    for r in rows:
        rid = str(r.get("id") or "")
        label = (
            str(r.get("name")
                or r.get("directive")
                or r.get("claim")
                or r.get("match_value")
                or r.get("target_name")
                or "")
        )[:40]
        deleted_at = (r.get("deleted_at") or "")[:19]
        table_rows.append([f"#{rid}", label, deleted_at])
    table = fenced_table(table_rows, headers=["id", "label", "deleted_at"])

    emb = build_embed(ReplyPayload(
        mode=Mode.ADMIN_DELETED,
        title=f"Tombstoned `{kind.value}` ({len(rows)})",
        description=table,
        subtext=f"admin/deleted · {kind.value} · newest first",
        footer="Pick one from the menu to restore, or hit Undelete all.",
    ))
    await interaction.response.send_message(embed=emb, view=UndeleteView(kind.value, rows))


@admin_group.command(name="undelete", description="Restore one soft-deleted row by id.")
@app_commands.describe(kind="Which table.", row_id="Tombstoned row id.")
@app_commands.choices(kind=_kind_choices())
async def admin_undelete(
    interaction: discord.Interaction,
    kind: app_commands.Choice[str],
    row_id: int,
) -> None:
    if not _is_owner(interaction):
        await _deny(interaction)
        return
    try:
        ok = db.admin_undelete(kind.value, int(row_id))
    except ValueError as e:
        await interaction.response.send_message(str(e), ephemeral=True)
        return
    except Exception:
        log.exception("[slash] undelete failed")
        await interaction.response.send_message("Undelete errored.", ephemeral=True)
        return
    if not ok:
        await interaction.response.send_message(
            f"No tombstoned `{kind.value}` #{row_id}.", ephemeral=True,
        )
        return
    log.info("[admin] %s restored %s #%s via slash", interaction.user, kind.value, row_id)
    emb = build_embed(ReplyPayload(
        mode=Mode.ADMIN_UNDELETE,
        title=f"Restored #{row_id}",
        fields=(
            EmbedField("Kind", kind.value, inline=True),
            EmbedField("ID", f"`{row_id}`", inline=True),
            EmbedField("State", "live · tombstone gone", inline=True),
        ),
        subtext=f"admin/undelete · {kind.value}#{row_id}",
        footer="Back in the library as if it never left",
    ))
    await interaction.response.send_message(embed=emb)


@admin_group.command(name="undelete-all", description="Restore every tombstoned row of one kind.")
@app_commands.describe(kind="Which table.")
@app_commands.choices(kind=_kind_choices())
async def admin_undelete_all(
    interaction: discord.Interaction,
    kind: app_commands.Choice[str],
) -> None:
    if not _is_owner(interaction):
        await _deny(interaction)
        return
    try:
        n = db.admin_undelete_all(kind.value)
    except ValueError as e:
        await interaction.response.send_message(str(e), ephemeral=True)
        return
    except Exception:
        log.exception("[slash] undelete-all failed")
        await interaction.response.send_message("Undelete-all errored.", ephemeral=True)
        return
    log.info("[admin] %s restored ALL %s (%s rows) via slash", interaction.user, kind.value, n)
    emb = build_embed(ReplyPayload(
        mode=Mode.ADMIN_UNDELETE,
        title=f"Restored {n} `{kind.value}` row(s)",
        fields=(
            EmbedField("Kind", kind.value, inline=True),
            EmbedField("Rows", str(n), inline=True),
        ),
        subtext=f"admin/undelete-all · {kind.value}",
    ))
    await interaction.response.send_message(embed=emb)


@admin_group.command(name="panic", description="Soft-clear personas/facts/triggers/grudges. Recoverable.")
@app_commands.describe(include_sounds="Also tombstone the sounds table (default: no).")
async def admin_panic(
    interaction: discord.Interaction,
    include_sounds: bool = False,
) -> None:
    if not _is_owner(interaction):
        await _deny(interaction)
        return

    stats = db.admin_stats()
    preview_kinds = ["personas", "facts", "triggers", "grudges"]
    if include_sounds:
        preview_kinds.append("sounds")
    rows = [[k, str(stats.get(k, {}).get("live", 0))] for k in preview_kinds]
    table = fenced_table(rows, headers=["kind", "live (will tombstone)"])

    emb = build_embed(ReplyPayload(
        mode=Mode.ADMIN_PANIC,
        title="About to soft-clear everything",
        description=table,
        fields=(
            EmbedField(
                "Reversible?",
                "Yes — `/halbot-admin undelete-all <kind>` restores. Only `/halbot-admin purge` is permanent.",
                inline=False,
            ),
            EmbedField(
                "Sounds",
                "Included." if include_sounds else "Not touched (re-uploading is expensive).",
                inline=False,
            ),
        ),
        subtext="admin/panic · confirmation required",
        footer="Confirm opens a modal — type PANIC to proceed.",
    ))
    await interaction.response.send_message(
        embed=emb, view=PanicConfirmView(include_sounds=include_sounds),
    )


@admin_group.command(name="purge", description="PERMANENT delete of tombstoned rows. Irreversible.")
@app_commands.describe(
    kind="Which table.",
    older_than_days="Only purge tombstones older than this many days (optional).",
)
@app_commands.choices(kind=_kind_choices())
async def admin_purge(
    interaction: discord.Interaction,
    kind: app_commands.Choice[str],
    older_than_days: int | None = None,
) -> None:
    if not _is_owner(interaction):
        await _deny(interaction)
        return
    try:
        n = db.admin_hard_purge(kind.value, older_than_days)
    except ValueError as e:
        await interaction.response.send_message(str(e), ephemeral=True)
        return
    except Exception:
        log.exception("[slash] purge failed")
        await interaction.response.send_message("Purge errored.", ephemeral=True)
        return
    log.warning(
        "[admin] %s hard-purged %s (%s rows, older_than=%s) via slash",
        interaction.user, kind.value, n, older_than_days,
    )
    scope = f" older than {older_than_days}d" if older_than_days is not None else ""
    emb = build_embed(ReplyPayload(
        mode=Mode.ADMIN_PANIC,
        title=f"Purged {n} `{kind.value}` row(s)",
        description=f"Permanently removed tombstoned `{kind.value}`{scope}. Irreversible.",
        fields=(
            EmbedField("Kind", kind.value, inline=True),
            EmbedField("Rows", str(n), inline=True),
            EmbedField("Filter", scope.strip() or "(all tombstones)", inline=True),
        ),
        subtext=f"admin/purge · {kind.value}",
        footer="No undo — this bypassed the tombstone.",
    ))
    await interaction.response.send_message(embed=emb)


wake_variants_group = app_commands.Group(
    name="wake-variants",
    description="Manage wake-word substring dictionary (LLM-generated + manual).",
    parent=admin_group,
)


def _format_variants_table(rows: list[dict]) -> str:
    if not rows:
        return "(empty)"
    return fenced_table(
        [[r["token"], r["source"]] for r in rows],
        headers=["token", "source"],
    )


@wake_variants_group.command(
    name="generate",
    description="LLM-generate variants of the wake word; replaces only the 'llm' slice.",
)
@app_commands.describe(
    word="Override the wake word (default: 'robot').",
)
async def wake_variants_generate(
    interaction: discord.Interaction,
    word: str | None = None,
) -> None:
    if not _is_owner(interaction):
        await _deny(interaction)
        return
    target = (word or _voice.WAKE_WORD or "robot").strip().lower()
    if not target:
        await interaction.response.send_message("Word is empty.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        variants = await generate_wake_variants_async(target)
    except Exception as e:
        log.exception("[slash] wake-variants generate failed")
        await interaction.followup.send(
            f"LLM generation failed — dictionary unchanged.\n`{type(e).__name__}: {e}`",
            ephemeral=True,
        )
        return

    before = set(db.wake_variant_tokens())
    try:
        n = db.wake_variant_replace_llm(variants)
    except Exception:
        log.exception("[slash] wake-variants replace failed")
        await interaction.followup.send(
            "Storing variants failed — dictionary unchanged.",
            ephemeral=True,
        )
        return
    after = set(db.wake_variant_tokens())
    added = sorted(after - before)
    removed = sorted(before - after)
    log.info(
        "[admin] %s ran wake-variants generate %r → %d items (+%d / -%d)",
        interaction.user, target, n, len(added), len(removed),
    )

    rows = db.wake_variant_list()
    emb = build_embed(ReplyPayload(
        mode=Mode.ADMIN_UNDELETE,
        title=f"Wake variants regenerated for `{target}`",
        description=_format_variants_table(rows),
        fields=(
            EmbedField("Word", target, inline=True),
            EmbedField("LLM rows", str(n), inline=True),
            EmbedField("Total", str(len(rows)), inline=True),
            EmbedField(
                "Added",
                ", ".join(f"`{t}`" for t in added[:20]) or "(none)",
                inline=False,
            ),
            EmbedField(
                "Removed",
                ", ".join(f"`{t}`" for t in removed[:20]) or "(none)",
                inline=False,
            ),
        ),
        subtext=f"admin/wake-variants/generate · {target}",
        footer="Seed + manual rows untouched. Picked up on next utterance.",
    ))
    await interaction.followup.send(embed=emb, ephemeral=True)


@wake_variants_group.command(name="list", description="Show current wake-variant dictionary.")
async def wake_variants_list(interaction: discord.Interaction) -> None:
    if not _is_owner(interaction):
        await _deny(interaction)
        return
    try:
        rows = db.wake_variant_list()
    except Exception:
        log.exception("[slash] wake-variants list failed")
        await interaction.response.send_message("List query failed.", ephemeral=True)
        return
    by_source: dict[str, int] = {}
    for r in rows:
        by_source[r["source"]] = by_source.get(r["source"], 0) + 1
    summary = " · ".join(f"{k}={v}" for k, v in sorted(by_source.items())) or "(empty)"
    emb = build_embed(ReplyPayload(
        mode=Mode.ADMIN_STATUS,
        title=f"Wake variants ({len(rows)})",
        description=_format_variants_table(rows),
        subtext=f"admin/wake-variants/list · {summary}",
    ))
    await interaction.response.send_message(embed=emb, ephemeral=True)


@wake_variants_group.command(name="add", description="Add a manual wake-variant token.")
@app_commands.describe(token="Substring to match (lowercased automatically).")
async def wake_variants_add(
    interaction: discord.Interaction,
    token: str,
) -> None:
    if not _is_owner(interaction):
        await _deny(interaction)
        return
    try:
        added = db.wake_variant_add(token)
    except ValueError as e:
        await interaction.response.send_message(str(e), ephemeral=True)
        return
    except Exception:
        log.exception("[slash] wake-variants add failed")
        await interaction.response.send_message("Add errored.", ephemeral=True)
        return
    n = (token or "").strip().lower()
    if not added:
        await interaction.response.send_message(
            f"`{n}` already in dictionary.", ephemeral=True,
        )
        return
    log.info("[admin] %s added wake-variant %r", interaction.user, n)
    await interaction.response.send_message(
        f"Added `{n}` (source=manual). Picked up on next utterance.",
        ephemeral=True,
    )


@wake_variants_group.command(name="remove", description="Remove a wake-variant token by exact match.")
@app_commands.describe(token="Token to remove (case-insensitive).")
async def wake_variants_remove(
    interaction: discord.Interaction,
    token: str,
) -> None:
    if not _is_owner(interaction):
        await _deny(interaction)
        return
    try:
        removed = db.wake_variant_remove(token)
    except Exception:
        log.exception("[slash] wake-variants remove failed")
        await interaction.response.send_message("Remove errored.", ephemeral=True)
        return
    n = (token or "").strip().lower()
    if not removed:
        await interaction.response.send_message(
            f"No such variant `{n}`.", ephemeral=True,
        )
        return
    log.info("[admin] %s removed wake-variant %r", interaction.user, n)
    await interaction.response.send_message(
        f"Removed `{n}`. Picked up on next utterance.",
        ephemeral=True,
    )


@wake_variants_group.command(
    name="clear",
    description="Drop every wake-variant except the original seed list.",
)
async def wake_variants_clear(interaction: discord.Interaction) -> None:
    if not _is_owner(interaction):
        await _deny(interaction)
        return
    try:
        n = db.wake_variant_clear()
    except Exception:
        log.exception("[slash] wake-variants clear failed")
        await interaction.response.send_message("Clear errored.", ephemeral=True)
        return
    log.warning("[admin] %s cleared %d wake-variant rows", interaction.user, n)
    rows = db.wake_variant_list()
    emb = build_embed(ReplyPayload(
        mode=Mode.ADMIN_PANIC,
        title=f"Cleared {n} non-seed wake-variant row(s)",
        description=_format_variants_table(rows),
        subtext="admin/wake-variants/clear",
        footer="Seed rows preserved so wake detection cannot break.",
    ))
    await interaction.response.send_message(embed=emb, ephemeral=True)


@app_commands.command(
    name="halbot-stats",
    description="Publish a public snapshot of Halbot's stats and reply with the URL.",
)
@app_commands.guild_only()
async def halbot_stats(interaction: discord.Interaction) -> None:
    await interaction.response.defer(thinking=True)
    client = interaction.client
    user_id = int(getattr(interaction.user, "id", 0) or 0)
    # Pre-resolve user labels async so the sync snapshot path doesn't
    # fall back to `user_NNNN` for IDs not in discord.py's caches.
    try:
        uids = stats_publisher.collect_user_ids_for_resolution()
        user_labels = await stats_publisher.resolve_user_labels(client, uids)
    except Exception as e:
        log.warning("[slash] /halbot-stats label pre-resolve failed: %s", e)
        user_labels = {}
    try:
        result = await asyncio.to_thread(
            stats_publisher.publish_now, client,
            user_id=user_id, user_label_cache=user_labels,
        )
    except Exception as e:
        log.exception("[slash] /halbot-stats publish failed")
        err_line = str(e).splitlines()[0] if str(e) else ""
        emb = build_embed(ReplyPayload(
            mode=Mode.ERROR,
            title="Stats snapshot failed",
            description=f"`{type(e).__name__}: {err_line[:300]}`",
            subtext="halbot-stats · publish error",
            footer="Check daemon logs for details.",
        ))
        await interaction.followup.send(embed=emb)
        return

    cached_tag = " (cached)" if result.cached else ""
    payload = ReplyPayload(
        mode=Mode.NOTED,
        title="Halbot stats",
        description=f"Snapshot ready:\n{result.url}",
        subtext=f"halbot-stats · {result.file_count} files · {result.bytes_uploaded // 1024} KB",
        footer=f"Generated {result.generated_at_utc}{cached_tag}",
    )
    # interaction.followup is a Webhook — bypass send_halbot_reply (which
    # branches on Message/Messageable) and send directly so the subtext line
    # still rides as a content prefix above the embed.
    emb = build_embed(payload)
    content = f"-# *{payload.subtext}*" if payload.subtext else None
    await interaction.followup.send(content=content, embed=emb)
    log.info(
        "[slash] /halbot-stats by %s → %s%s",
        interaction.user, result.url, cached_tag,
    )


def register_slash(client: discord.Client) -> app_commands.CommandTree:
    """Attach the slash tree + admin group to the client.

    Call once from ``build_client``. Tree sync happens in ``on_ready``
    (sync is a network call and must run after login).
    """
    tree = app_commands.CommandTree(client)
    tree.add_command(admin_group)
    tree.add_command(halbot_stats)
    log.info(
        "[slash] command tree registered (/%s, /%s)",
        admin_group.name, halbot_stats.name,
    )
    return tree
