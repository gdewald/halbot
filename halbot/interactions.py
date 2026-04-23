"""Persistent discord.ui.View implementations for Halbot flows.

Each View here is registered on client boot via ``register_persistent_views``
so its buttons survive restarts. Custom IDs are stable strings — never
include per-message data like sound names in them; resolve those from
the embed at interaction time.

Phase 1 scope: SoundboardActionsView (Stop / Replay / Louder).
Other views land in later phases per docs/plans/014.
"""
from __future__ import annotations

import logging

import discord

log = logging.getLogger("halbot.interactions")


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


def register_persistent_views(client: discord.Client) -> None:
    """Attach every long-lived view to the client so buttons survive restart.

    Call once from ``on_ready``. Safe to call repeatedly; discord.py dedupes.
    """
    client.add_view(SoundboardActionsView())
    log.info("[interactions] persistent views registered")
