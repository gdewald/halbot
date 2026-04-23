"""discord.ui.View + Modal implementations for Halbot flows.

Persistent views (stable ``custom_id``, no per-instance state) are
registered on client boot via ``register_persistent_views`` so their
buttons survive restart. Transient views (admin tombstone selects,
panic confirmations) carry their own state and time out naturally.

Views here are intentionally dumb — they call back into ``halbot.bot``
/ ``halbot.db`` on interaction and don't hold business logic.
"""
from __future__ import annotations

import logging

import discord

log = logging.getLogger("halbot.interactions")


def _is_guild_owner(interaction: discord.Interaction) -> bool:
    guild = interaction.guild
    if not guild:
        return False
    return getattr(guild, "owner_id", None) == interaction.user.id


class SoundboardActionsView(discord.ui.View):
    """Stop / Replay / Louder controls under a soundboard.play embed.

    Actions read the played sound name from the embed title
    (``"▶ Playing <name>"``) so we don't need to stash per-row state.
    """

    def __init__(self) -> None:
        super().__init__(timeout=None)

    @staticmethod
    def _sound_name_from(interaction: discord.Interaction) -> str | None:
        msg = interaction.message
        if not msg or not msg.embeds:
            return None
        title = msg.embeds[0].title or ""
        prefix = "▶ Playing "
        if title.startswith(prefix):
            return title[len(prefix):].strip()
        return None

    @discord.ui.button(
        label="Stop", style=discord.ButtonStyle.secondary,
        emoji="⏹", custom_id="halbot:sb:stop",
    )
    async def stop(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        from .voice_session import voice_listeners
        guild = interaction.guild
        session = voice_listeners.get(guild.id) if guild else None
        if session and session.vc.is_connected() and session.vc.is_playing():
            session.vc.stop()
            await interaction.response.send_message("Stopped.", ephemeral=True)
        else:
            await interaction.response.send_message("Nothing playing.", ephemeral=True)

    @discord.ui.button(
        label="Replay", style=discord.ButtonStyle.secondary,
        emoji="↺", custom_id="halbot:sb:replay",
    )
    async def replay(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        from .db import db_get
        from .audio import detect_audio_format
        from .voice_session import voice_listeners

        guild = interaction.guild
        name = self._sound_name_from(interaction)
        if not guild or not name:
            await interaction.response.send_message("Can't resolve original sound.", ephemeral=True)
            return
        session = voice_listeners.get(guild.id)
        if not session or not session.vc.is_connected():
            await interaction.response.send_message("I'm not in voice.", ephemeral=True)
            return
        row = db_get(name)
        audio = row["audio"] if row else None
        if not audio:
            try:
                sounds = list(await guild.fetch_soundboard_sounds())
                match = next((s for s in sounds if s.name == name), None)
                if match:
                    audio = await match.read()
            except Exception:
                log.exception("[replay] live-sound fetch failed for %r", name)
        if not audio:
            await interaction.response.send_message(f"Couldn't find `{name}`.", ephemeral=True)
            return
        await session.play_sound(audio, detect_audio_format(audio))
        await interaction.response.send_message(f"Replaying `{name}`.", ephemeral=True)

    @discord.ui.button(
        label="Louder", style=discord.ButtonStyle.secondary,
        emoji="🔊", custom_id="halbot:sb:louder",
    )
    async def louder(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        # Volume knob is not wired into VoiceSession yet — surface a friendly
        # stub so the button still feels responsive.
        await interaction.response.send_message(
            "Volume control isn't wired up yet. Ping the owner.",
            ephemeral=True,
        )


class AdminStatusView(discord.ui.View):
    """Owner-only jump buttons under an /halbot-admin status embed.

    Each button defers to a slash subcommand by telling the owner what
    to run next — we deliberately avoid chaining slash commands from
    buttons because Discord doesn't expose that. Keep the button a
    gentle hint, not an implicit state mutation.
    """

    def __init__(self) -> None:
        super().__init__(timeout=None)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not _is_guild_owner(interaction):
            await interaction.response.send_message(
                "Owner-only.", ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(
        label="View deleted", style=discord.ButtonStyle.secondary,
        emoji="🪦", custom_id="halbot:admin:view_deleted",
    )
    async def view_deleted(self, interaction, button) -> None:  # type: ignore[no-untyped-def]
        await interaction.response.send_message(
            "Run `/halbot-admin deleted` and pick a kind.", ephemeral=True,
        )

    @discord.ui.button(
        label="Undelete…", style=discord.ButtonStyle.secondary,
        emoji="↶", custom_id="halbot:admin:undelete_hint",
    )
    async def undelete_hint(self, interaction, button) -> None:  # type: ignore[no-untyped-def]
        await interaction.response.send_message(
            "Run `/halbot-admin undelete` with kind + row id, or "
            "`/halbot-admin deleted` to pick from a menu.",
            ephemeral=True,
        )

    @discord.ui.button(
        label="Panic…", style=discord.ButtonStyle.danger,
        emoji="⚠️", custom_id="halbot:admin:panic_hint",
    )
    async def panic_hint(self, interaction, button) -> None:  # type: ignore[no-untyped-def]
        await interaction.response.send_message(
            "Run `/halbot-admin panic` (add `all:true` to also nuke sounds).",
            ephemeral=True,
        )


class UndeleteView(discord.ui.View):
    """Transient view rendered alongside a tombstone listing.

    Carries per-command state: the ``kind`` being browsed and the
    tombstone rows. Times out after 5 minutes so stale menus don't
    linger across server restarts.
    """

    TIMEOUT_SECONDS = 300

    def __init__(self, kind: str, rows: list[dict]) -> None:
        super().__init__(timeout=self.TIMEOUT_SECONDS)
        self.kind = kind
        self.rows = rows

        if rows:
            options: list[discord.SelectOption] = []
            for r in rows[:25]:
                rid = r.get("id")
                label = (
                    str(r.get("name")
                        or r.get("directive")
                        or r.get("claim")
                        or r.get("match_value")
                        or r.get("target_name")
                        or f"#{rid}")
                )[:100]
                desc = (r.get("deleted_at") or "")[:100]
                options.append(discord.SelectOption(
                    label=label, description=desc or None,
                    value=str(rid), emoji="🪦",
                ))
            self.add_item(_UndeleteSelect(kind, options))

        self.add_item(_UndeleteAllButton(kind))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not _is_guild_owner(interaction):
            await interaction.response.send_message("Owner-only.", ephemeral=True)
            return False
        return True


class _UndeleteSelect(discord.ui.Select):
    def __init__(self, kind: str, options: list[discord.SelectOption]) -> None:
        super().__init__(
            placeholder="Undelete one…",
            min_values=1, max_values=1,
            options=options,
            custom_id=f"halbot:admin:undelete_select:{kind}",
        )
        self.kind = kind

    async def callback(self, interaction: discord.Interaction) -> None:
        from .db import admin_undelete
        from .bot_ui import Mode, ReplyPayload, EmbedField, send_halbot_reply
        try:
            row_id = int(self.values[0])
        except ValueError:
            await interaction.response.send_message("Bad row id.", ephemeral=True)
            return
        try:
            ok = admin_undelete(self.kind, row_id)
        except ValueError as e:
            await interaction.response.send_message(str(e), ephemeral=True)
            return
        if not ok:
            await interaction.response.send_message(
                f"No tombstoned `{self.kind}` #{row_id} found.", ephemeral=True,
            )
            return
        log.info("[admin] %s restored %s #%s via select", interaction.user, self.kind, row_id)
        await interaction.response.send_message(
            embed=_restored_embed(self.kind, row_id),
            ephemeral=False,
        )


def _restored_embed(kind: str, row_id: int) -> discord.Embed:
    from .bot_ui import Mode, ReplyPayload, EmbedField, build_embed
    return build_embed(ReplyPayload(
        mode=Mode.ADMIN_UNDELETE,
        title=f"Restored #{row_id}",
        fields=(
            EmbedField("Kind", kind, inline=True),
            EmbedField("ID", f"`{row_id}`", inline=True),
            EmbedField("State", "live · tombstone gone", inline=True),
        ),
        subtext=f"admin/undelete · {kind}#{row_id}",
        footer="Back in the library as if it never left",
    ))


class _UndeleteAllButton(discord.ui.Button):
    def __init__(self, kind: str) -> None:
        super().__init__(
            style=discord.ButtonStyle.success,
            label=f"Undelete all {kind}",
            emoji="↶",
            custom_id=f"halbot:admin:undelete_all:{kind}",
        )
        self.kind = kind

    async def callback(self, interaction: discord.Interaction) -> None:
        from .db import admin_undelete_all
        from .bot_ui import Mode, ReplyPayload, EmbedField, build_embed
        try:
            n = admin_undelete_all(self.kind)
        except ValueError as e:
            await interaction.response.send_message(str(e), ephemeral=True)
            return
        log.info("[admin] %s restored ALL %s (%s rows) via button", interaction.user, self.kind, n)
        emb = build_embed(ReplyPayload(
            mode=Mode.ADMIN_UNDELETE,
            title=f"Restored {n} `{self.kind}` row(s)",
            fields=(EmbedField("Kind", self.kind, inline=True),
                    EmbedField("Rows", str(n), inline=True)),
            subtext=f"admin/undelete-all · {self.kind}",
        ))
        await interaction.response.send_message(embed=emb)


class PanicConfirmView(discord.ui.View):
    """Renders under the panic-preview embed. Clicking Confirm opens a modal
    that demands the code word ``PANIC``."""

    TIMEOUT_SECONDS = 300

    def __init__(self, include_sounds: bool) -> None:
        super().__init__(timeout=self.TIMEOUT_SECONDS)
        self.include_sounds = include_sounds

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not _is_guild_owner(interaction):
            await interaction.response.send_message("Owner-only.", ephemeral=True)
            return False
        return True

    @discord.ui.button(
        label="Confirm panic…", style=discord.ButtonStyle.danger, emoji="⚠️",
    )
    async def confirm(self, interaction, button) -> None:  # type: ignore[no-untyped-def]
        await interaction.response.send_modal(PanicModal(self.include_sounds))

    @discord.ui.button(
        label="Cancel", style=discord.ButtonStyle.secondary, emoji="✕",
    )
    async def cancel(self, interaction, button) -> None:  # type: ignore[no-untyped-def]
        for child in self.children:
            if hasattr(child, "disabled"):
                child.disabled = True
        await interaction.response.edit_message(view=self)
        await interaction.followup.send("Panic cancelled.", ephemeral=True)


class PanicModal(discord.ui.Modal, title="Panic confirmation"):
    code_word = discord.ui.TextInput(
        label="Code word",
        placeholder="Type PANIC to confirm",
        required=True, max_length=16,
    )
    reason = discord.ui.TextInput(
        label="Reason (audit log)",
        placeholder="what happened?",
        required=False, max_length=200,
    )

    def __init__(self, include_sounds: bool) -> None:
        super().__init__(timeout=180)
        self.include_sounds = include_sounds

    async def on_submit(self, interaction: discord.Interaction) -> None:
        from .db import admin_panic_clear
        from .bot_ui import Mode, ReplyPayload, EmbedField, build_embed

        if str(self.code_word).strip() != "PANIC":
            await interaction.response.send_message(
                "Code word mismatch. Panic aborted.", ephemeral=True,
            )
            return

        kinds = ["personas", "facts", "triggers", "grudges"]
        if self.include_sounds:
            kinds.append("sounds")
        try:
            result = admin_panic_clear(kinds)
        except Exception:
            log.exception("[admin] panic_clear failed")
            await interaction.response.send_message(
                "Panic failed — check logs.", ephemeral=True,
            )
            return

        total = sum(result.values())
        log.warning(
            "[admin] %s invoked panic (include_sounds=%s, reason=%r): %s",
            interaction.user, self.include_sounds,
            str(self.reason).strip() or "(none)", result,
        )
        cleared_str = " · ".join(f"`{k}` ×{n}" for k, n in result.items())
        emb = build_embed(ReplyPayload(
            mode=Mode.PANIC_COMPLETE,
            title="Soft-cleared. Take a breath.",
            fields=(
                EmbedField("Tombstoned", cleared_str or "(nothing)", inline=False),
                EmbedField(
                    "Undo",
                    "`/halbot-admin undelete-all` per kind you want back",
                    inline=False,
                ),
                EmbedField(
                    "Permanent?",
                    "No — only after `/halbot-admin purge`.",
                    inline=False,
                ),
            ),
            subtext=f"admin/panic complete · {total} row(s) tombstoned",
            footer="No outgoing Discord side-effects · state is store-only",
        ))
        await interaction.response.send_message(embed=emb)


def register_persistent_views(client: discord.Client) -> None:
    """Attach long-lived views to the client so their buttons survive
    restart. Called from ``on_ready``; safe to call repeatedly.

    Only views with stable ``custom_id`` strings and no per-instance state
    belong here. Parameterized views (UndeleteView, PanicConfirmView)
    are created on-demand by slash handlers and time out naturally.
    """
    client.add_view(SoundboardActionsView())
    client.add_view(AdminStatusView())
    log.info("[interactions] persistent views registered")
