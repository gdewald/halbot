import asyncio
import base64
import json
import logging
import re
from pathlib import Path

import requests

from .db import emoji_db_list, fact_list, grudge_list, persona_list, trigger_list

log = logging.getLogger("halbot")

from . import config as _config

LLM_URL = _config.get("llm_url")
LLM_MODEL = _config.get("llm_model") or "gemma4:e4b"


def _keepalive_value() -> str | None:
    """Ollama `keep_alive` body parameter — string like "10m". None disables."""
    try:
        mins = int(_config.get("llm_keepalive_minutes"))
    except (TypeError, ValueError):
        mins = 10
    if mins <= 0:
        return None
    return f"{mins}m"


def _apply_model_and_keepalive(body: dict) -> dict:
    """Inject `model` and ollama-specific `keep_alive` on every request body.

    `keep_alive` is ignored by non-ollama OpenAI-compat servers, so it's safe
    to send unconditionally.
    """
    if LLM_MODEL:
        body["model"] = LLM_MODEL
    ka = _keepalive_value()
    if ka is not None:
        body["keep_alive"] = ka
    return body


PERSONA_STACKING_GUIDE = (
    "PERSONA STACKING: when MULTIPLE directives are listed, you MUST honor "
    "ALL of them simultaneously in every response — do not pick one and "
    "drop the others. Treat them as composable constraints and combine "
    "creatively. E.g. 'speak like a pirate' + 'reply only in haiku' → "
    "output a 5-7-5 haiku written in pirate voice. 'invent a fictional "
    "language' + 'haiku only' → compose a haiku in the invented language. "
    "If two directives seem to clash, find the most creative overlap that "
    "satisfies both at once; never silently ignore one. Best-effort is "
    "fine — a weird hybrid is better than obeying just one."
)


def _format_facts_block(empty: str = "(none)") -> str:
    """Render canonical facts as a plain-text list for prompt injection."""
    try:
        rows = fact_list()
    except Exception:
        rows = []
    if not rows:
        return empty
    lines = []
    for r in rows:
        lines.append(f"- [#{r['id']}] {r['subject']}: {r['claim']} (set by {r['set_by']})")
    return "\n".join(lines)


def _format_grudges_block(empty: str = "(none)") -> str:
    try:
        rows = grudge_list()
    except Exception:
        rows = []
    if not rows:
        return empty
    lines = []
    for r in rows:
        pol = r["polarity"]
        if pol > 0:
            tag = f"devotion+{pol}"
        elif pol < 0:
            tag = f"grudge{pol}"
        else:
            tag = "neutral"
        note = f" — {r['note']}" if r.get("note") else ""
        lines.append(f"- [#{r['id']}] {r['target_name']} ({tag}){note} (set by {r['set_by']})")
    return "\n".join(lines)


def _format_triggers_block(empty: str = "(none)") -> str:
    """Render installed triggers for prompt visibility (not auto-executed here)."""
    try:
        rows = trigger_list()
    except Exception:
        rows = []
    if not rows:
        return empty
    lines = []
    for r in rows:
        lines.append(
            f"- [#{r['id']}] on {r['match_kind']}=\"{r['match_value']}\" → "
            f"{r['action_type']}:{r['action_payload']} (set by {r['set_by']}, "
            f"fired {r.get('fire_count', 0)}x)"
        )
    return "\n".join(lines)


def _format_persona_block(header: str = "ACTIVE BEHAVIOR DIRECTIVES",
                          *, include_stacking: bool = True,
                          empty: str = "") -> str:
    """Render the current persona list for prompt injection.

    Returns empty string when no directives are set (unless `empty` given).
    Always includes PERSONA_STACKING_GUIDE when ≥2 directives are active
    (the instruction is harmless with 1, so we include it uniformly to
    encourage future stacking).
    """
    try:
        personas = persona_list()
    except Exception:
        personas = []
    if not personas:
        return empty
    lines = [f"{header}:"] + [f"- {p['directive']}" for p in personas]
    if include_stacking:
        lines.append("")
        lines.append(PERSONA_STACKING_GUIDE)
    return "\n".join(lines)

# Reasoning models can take >30s for a single response. Keep both read
# timeouts generous so a slow generation doesn't surface as "I didn't
# understand that".
LLM_TIMEOUT = 120
LLM_RETRY_TIMEOUT = 180

def chat_history_limit() -> int:
    try:
        return max(0, int(_config.get("chat_history_limit")))
    except (TypeError, ValueError):
        return 50

import time as _time
_REACH_CACHE: dict = {"ok": False, "ts": 0.0}
_REACH_TTL = 5.0


def is_reachable_cached() -> bool:
    """Cheap HEAD to LLM backend, cached 5s. False on any error."""
    now = _time.time()
    if now - _REACH_CACHE["ts"] < _REACH_TTL:
        return _REACH_CACHE["ok"]
    ok = False
    try:
        base = LLM_URL.split("/v1/")[0]
        r = requests.get(base + "/v1/models", timeout=0.5)
        ok = r.status_code == 200
    except Exception:
        ok = False
    _REACH_CACHE["ok"] = ok
    _REACH_CACHE["ts"] = now
    return ok

SYSTEM_PROMPT = (Path(__file__).parent / "prompts" / "system_prompt.txt").read_text(encoding="utf-8")

VOICE_COMMAND_PROMPT = """\
You are a Discord soundboard bot. A user in the voice channel said the wake word \
"Halbot" followed by a command. Pick the best sound to play.

SAVED LIBRARY:
{saved_details}

LIVE SOUNDBOARD:
{sound_details}

{persona_directives_block}

Return JSON — one of:
- Sound request with a match: {{"action": "voice_play", "name": "<exact sound name>"}}
- Conversational / question / chitchat (e.g. "are you ok", "how's it \
going", "tell me a joke"): {{"action": "conversation"}} (a second \
smarter LLM pass will generate the spoken reply — do NOT write it here).
- Sound request with NO match on the lists above, OR unclear request: \
{{"action": "unknown", "message": "<brief response>"}}

Match creatively — "something scary" → pick a scary-sounding name, \
"play airhorn" → exact match. Names must be EXACT from the lists above. \
Prefer voice_play whenever a reasonable match exists (speed matters).

Reply with ONLY the JSON. No explanation.\
"""

RESPONSE_CUSTOMIZATION_PROMPT = """\
You are Halbot.  Rewrite the plain text message below so it sounds like
something you would say to the user in Discord, shaped by the active
persona directives.

Rules:
- 1 to 2 sentences.  Never more.
- Preserve the original meaning — do not invent new facts or change the
  user-facing outcome.  If the original says something went wrong, yours
  must also convey that.
- Plain text only — no markdown, no JSON, no code blocks, no emoji
  (the caller adds any emoji).
- Do NOT quote the original verbatim; rewrite it in your voice.
{persona_directives_block}
"""


def _llm_base() -> str:
    """Strip the OpenAI path suffix to get the Ollama server root."""
    for marker in ("/v1/", "/api/"):
        idx = LLM_URL.find(marker)
        if idx != -1:
            return LLM_URL[:idx]
    return LLM_URL.rstrip("/")


def _keepalive_ping() -> bool:
    """One-shot Ollama keepalive ping. Empty prompt + keep_alive refreshes
    VRAM residency without inference. Returns True on 200, False on any error."""
    if not LLM_MODEL:
        return False
    ka = _keepalive_value()
    if ka is None:
        return False
    base = _llm_base()
    body = {"model": LLM_MODEL, "keep_alive": ka, "prompt": ""}
    try:
        r = requests.post(f"{base}/api/generate", json=body, timeout=10)
        ok = r.status_code == 200
        if not ok:
            log.warning("[llm-keepalive] %s: %s", r.status_code, r.text[:200])
        return ok
    except requests.RequestException as e:
        log.warning("[llm-keepalive] ping failed: %s", e)
        return False


async def keepalive_loop() -> None:
    """Periodically refresh Ollama's keep_alive timer so the model stays in VRAM
    during idle stretches. Interval and duration come from config; either set
    to 0 disables the loop."""
    try:
        interval = int(_config.get("llm_keepalive_interval_seconds"))
    except (TypeError, ValueError):
        interval = 240
    if interval <= 0 or _keepalive_value() is None:
        log.info("[llm-keepalive] disabled (interval=%ss)", interval)
        return
    log.info("[llm-keepalive] loop start: interval=%ss model=%s keep_alive=%s",
             interval, LLM_MODEL, _keepalive_value())
    while True:
        try:
            await asyncio.to_thread(_keepalive_ping)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("[llm-keepalive] unexpected error")
        try:
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            raise


def ensure_model_loaded(model: str = LLM_MODEL, timeout: int = 180) -> bool:
    """Check model is available in Ollama. Ollama auto-loads on inference — this
    is a connectivity + existence check only, not a JIT trigger."""
    base = _llm_base()
    try:
        resp = requests.get(f"{base}/api/tags", timeout=5)
        resp.raise_for_status()
        names = [m.get("name", "") for m in resp.json().get("models", [])]
        available = any(n == model or n.startswith(model + ":") for n in names)
        if not available:
            log.warning("Model %r not in Ollama /api/tags. Available: %s", model, names)
        return True  # let inference call surface the real error if model missing
    except requests.RequestException as e:
        log.warning("Could not query Ollama model list: %s", e)
        return False


def describe_emoji_image(image_bytes: bytes, name: str) -> str:
    """Send an emoji image to Ollama vision and get a short description."""
    b64 = base64.b64encode(image_bytes).decode()
    mime = "image/gif" if image_bytes[:4] == b"GIF8" else "image/png"
    body = {
        "messages": [
            {"role": "user", "content": [
                {"type": "text",
                 "text": f"This is a Discord custom emoji called '{name}'. "
                         "Describe what it depicts in one short sentence (under 100 characters). "
                         "Just the description, nothing else."},
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
            ]},
        ],
        "temperature": 0.3,
        "max_tokens": 60,
    }
    _apply_model_and_keepalive(body)
    body["reasoning_effort"] = "none"
    try:
        resp = requests.post(LLM_URL, json=body, timeout=LLM_TIMEOUT)
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        log.warning("Failed to describe emoji %s: %s", name, e)
        return ""


def format_sound_details(sounds) -> str:
    """Build a detailed listing of live sounds for the Ollama prompt."""
    if not sounds:
        return "(none)"
    lines = []
    for s in sounds:
        user_name = str(s.user) if s.user else "unknown"
        date_str = s.created_at.strftime("%Y-%m-%d") if s.created_at else "unknown"
        lines.append(f"- \"{s.name}\" (added by {user_name} on {date_str})")
    return "\n".join(lines)


def format_saved_details(saved: list[dict]) -> str:
    """Build a detailed listing of saved sounds for the Ollama prompt."""
    if not saved:
        return "(none)"
    lines = []
    for s in saved:
        parts = [f"\"{s['name']}\""]
        if s.get("emoji"):
            parts.append(f"emoji: {s['emoji']}")
        if s.get("saved_by"):
            parts.append(f"saved by {s['saved_by']}")
        parts.append(f"on {s['created_at']}")
        size_kb = round(s.get("size_bytes", 0) / 1024, 1)
        parts.append(f"{size_kb}KB")
        if s.get("effects"):
            try:
                fx = json.loads(s["effects"])
                parts.append(f"effects: {'+'.join(e['type'] for e in fx)}")
            except (json.JSONDecodeError, KeyError):
                pass
        if s.get("parent_id"):
            parts.append(f"derived from id:{s['parent_id']}")
        if s.get("metadata"):
            parts.append(f"notes: {s['metadata']}")
        lines.append(f"- {' | '.join(parts)}")
    return "\n".join(lines)


def parse_intent(user_text: str, sounds, saved: list[dict], channel_history: list[dict],
                  attachment_info: list[dict] | None = None,
                  guild=None,
                  voice_channels_str: str = "(unknown)",
                  voice_status_str: str = "Not connected to any voice channel.",
                  _stats_out: dict | None = None) -> list[dict]:
    """Send user text + context to Ollama, return a list of actions to execute."""
    from datetime import date

    emoji_records = emoji_db_list()
    if emoji_records:
        emoji_lines = []
        for e in emoji_records:
            prefix = "a" if e["animated"] else ""
            desc = f" — {e['description']}" if e.get("description") else ""
            emoji_lines.append(f"- {e['name']} → <{prefix}:{e['name']}:{e['emoji_id']}>{desc}")
        emojis_str = "\n".join(emoji_lines)
    else:
        emojis_str = "(none)"

    personas = persona_list()
    if personas:
        persona_lines = [f"- [id:{p['id']}] {p['directive']} (set by {p['set_by']})" for p in personas]
        persona_str = "\n".join(persona_lines) + "\n\n" + PERSONA_STACKING_GUIDE
    else:
        persona_str = "(none — use your default personality)"

    if attachment_info:
        att_lines = ["ATTACHMENTS on this message:"]
        for att in attachment_info:
            parts = [f"\"{att['filename']}\"", f"{att['size_kb']}KB"]
            if att.get("duration") is not None:
                parts.append(f"{att['duration']:.1f}s")
            if att.get("valid"):
                parts.append("valid for soundboard")
            else:
                parts.append(f"INVALID: {att.get('reason', 'unknown')}")
            att_lines.append(f"- {' | '.join(parts)}")
        attachments_str = "\n".join(att_lines)
    else:
        attachments_str = "No attachments on this message."

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT.format(
            sound_details=format_sound_details(sounds),
            saved_details=format_saved_details(saved),
            custom_emojis=emojis_str,
            persona_directives=persona_str,
            facts_block=_format_facts_block(),
            grudges_block=_format_grudges_block(),
            triggers_block=_format_triggers_block(),
            today=date.today().isoformat(),
            attachments=attachments_str,
            voice_channels=voice_channels_str,
            voice_status=voice_status_str,
        )},
        *channel_history,
        {"role": "user", "content": user_text},
    ]

    body = {
        "messages": messages,
        "temperature": 0.1,
        "max_tokens": 6144,
    }
    _apply_model_and_keepalive(body)

    try:
        _t_http = _time.monotonic()
        resp = requests.post(LLM_URL, json=body, timeout=LLM_TIMEOUT)
        if resp.status_code >= 400:
            log.warning("Ollama %s response: %s", resp.status_code, resp.text[:500])
            if resp.status_code in (400, 404, 409, 503):
                if ensure_model_loaded(LLM_MODEL):
                    log.info("Retrying chat completion after model load")
                    resp = requests.post(LLM_URL, json=body, timeout=LLM_RETRY_TIMEOUT)
                    if resp.status_code >= 400:
                        log.warning("Ollama retry %s response: %s",
                                    resp.status_code, resp.text[:500])
        resp.raise_for_status()
        _llm_ms = int((_time.monotonic() - _t_http) * 1000)
        raw_json = resp.json()
        log.debug("Ollama raw response: %s", json.dumps(raw_json, indent=2))
        choice = raw_json["choices"][0]
        finish_reason = choice.get("finish_reason", "unknown")
        usage = raw_json.get("usage", {})
        log.info("Ollama finish_reason=%s, usage=%s", finish_reason, usage)
        if _stats_out is not None:
            _stats_out["prompt_tokens"] = int(usage.get("prompt_tokens") or 0)
            _stats_out["completion_tokens"] = int(usage.get("completion_tokens") or 0)
            _stats_out["llm_ms"] = _llm_ms
            _stats_out["outcome"] = "ok"
        message_obj = choice.get("message", {}) or {}
        content = (message_obj.get("content") or "").strip()
        # Handle <think>…</think> wrappers — thinking models hide the JSON
        # after the closing tag; take whatever follows.
        if "<think>" in content and "</think>" in content:
            _, _, rest = content.partition("</think>")
            content = rest.strip()
        elif "</think>" in content:
            _, _, rest = content.partition("</think>")
            content = rest.strip()
        # Fallback: some servers surface the real JSON in reasoning_content
        # when the visible content is empty or think-only.
        if not content:
            reasoning = (message_obj.get("reasoning_content") or message_obj.get("reasoning") or "").strip()
            if reasoning:
                start = reasoning.find("{")
                end = reasoning.rfind("}")
                if 0 <= start < end:
                    content = reasoning[start:end + 1]
        log.info("Ollama content: %r", content)
        if not content:
            log.error("Ollama returned empty content (finish_reason=%s)", finish_reason)
            return [{"action": "error", "message": "Ollama returned an empty response — the prompt may be too long for the model's context window."}]
        # Strip markdown code fences if the model wraps its response
        if content.startswith("```"):
            content = content.split("\n", 1)[1] if "\n" in content else content[3:]
            if content.endswith("```"):
                content = content[:-3]
            content = content.strip()
            log.debug("After stripping code fences: %s", content)
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            if content.lstrip().startswith("[BOT REPLY"):
                log.warning("Ollama mimicked BOT REPLY prefix, dropping: %s", content[:200])
                return [{"action": "error", "message": "LLM returned a mimicked prior reply instead of a JSON action. Try again."}]
            log.warning("Ollama returned non-JSON, treating as unknown: %s", content[:200])
            return [{"action": "unknown", "message": content}]
        log.info("Parsed actions: %s", json.dumps(parsed, indent=2))
        if isinstance(parsed, dict):
            return [parsed]
        if isinstance(parsed, list):
            return parsed
        return [{"action": "unknown", "message": str(parsed)}]
    except requests.exceptions.Timeout:
        log.error("Ollama timed out at %s", LLM_URL)
        if _stats_out is not None:
            _stats_out["outcome"] = "timeout"
        return [{"action": "error", "message": "Ollama timed out — try a shorter prompt or restart the model."}]
    except requests.ConnectionError:
        log.error("Could not connect to Ollama at %s", LLM_URL)
        if _stats_out is not None:
            _stats_out["outcome"] = "connect_error"
        return [{"action": "error", "message": "I'm having trouble thinking right now — is Ollama running?"}]
    except (requests.RequestException, KeyError, IndexError) as e:
        log.error("Failed to parse intent: %s", e)
        if _stats_out is not None:
            _stats_out["outcome"] = "error"
        return [{"action": "error", "message": f"Ollama call failed ({type(e).__name__}: {e}). Check Ollama server."}]


def customize_response(raw_text: str, *, context: str = "") -> str:
    """Rewrite a plain-text response via Ollama so it matches the active persona.

    Falls back to raw_text on any failure.
    """
    if not raw_text:
        return raw_text
    pd_block = _format_persona_block()
    if pd_block:
        pd_block = "\n" + pd_block
    system = RESPONSE_CUSTOMIZATION_PROMPT.format(persona_directives_block=pd_block)
    user_msg = f"Original: {raw_text}"
    if context:
        user_msg += f"\nContext: {context}"
    body = {
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user_msg},
        ],
        "temperature": 0.9,
        # Rewrite is 1-2 sentences; ~80 tokens of real output. 384 leaves
        # room for gemma4 to reason first in the `reasoning` channel.
        "max_tokens": 384,
        "think": False,
        "reasoning_effort": "none",
    }
    _apply_model_and_keepalive(body)
    try:
        resp = requests.post(LLM_URL, json=body, timeout=LLM_TIMEOUT)
        if resp.status_code >= 400:
            if resp.status_code in (400, 404, 409, 503):
                if ensure_model_loaded(LLM_MODEL):
                    resp = requests.post(LLM_URL, json=body, timeout=LLM_TIMEOUT)
        resp.raise_for_status()
        message = resp.json()["choices"][0].get("message", {})
        content = (message.get("content") or "").strip()
        # Handle <think>…</think> wrappers
        if "<think>" in content and "</think>" in content:
            _, _, rest = content.partition("</think>")
            content = rest.strip()
        # Fall back to reasoning_content tail if content got truncated
        if not content:
            reasoning = (message.get("reasoning_content") or message.get("reasoning") or "").strip()
            if reasoning:
                content = reasoning.splitlines()[-1].strip()
        # Strip surrounding quotes the model sometimes adds
        if len(content) >= 2 and content[0] == content[-1] in ('"', "'"):
            content = content[1:-1].strip()
        if not content:
            log.warning("[customize] empty content; returning raw text")
            return raw_text
        log.info("[customize] %r → %r", raw_text[:80], content[:120])
        return content
    except Exception as e:
        log.warning("[customize] failed (%s); returning raw text", e)
        return raw_text


async def customize_response_async(raw_text: str, *, context: str = "") -> str:
    """Async wrapper: runs customize_response in a worker thread."""
    return await asyncio.to_thread(customize_response, raw_text, context=context)


RICH_CUSTOMIZATION_PROMPT = """\
You are Halbot.  Produce a persona-voiced Discord reply for the user, shaped
by the active persona directives.  Return STRICT JSON with exactly these
two fields:

  {{"subtext": "<one short italic resolution line, plain text, no quotes>",
   "body": "<1-2 sentence reply in your voice>"}}

Rules:
- "subtext" is the italic lead-in that appears above the embed.  State
  briefly how you resolved the request (e.g. "Intent: soundboard.play ·
  target: taco-bell--screwed").  No opinions here.
- "body" is your persona-voiced reply. Plain text, no markdown fences,
  no JSON inside.  Keep it short (1-3 sentences) UNLESS the active persona
  demands structure (haiku = 3 lines 5-7-5, poem, list, ASCII art, etc.) —
  then preserve that structure using real newlines (\n) inside the JSON
  string so Discord renders them as line breaks.
- Preserve the original meaning AND the original line-break structure:
  if the Original text contains newlines, keep them in "body" unless a
  persona directive explicitly overrides format.
- Output JSON only — no prose before or after, no code fences.
{persona_directives_block}
"""


def customize_response_rich(raw_text: str, *, context: str = "",
                            resolution_hint: str = "") -> tuple[str, str]:
    """Return (subtext, body) — piggybacks one LLM call.

    ``subtext`` is the italic one-liner shown above Halbot's embed, stating
    how the request was resolved.  ``body`` is the persona-voiced reply
    that goes in the embed description.

    Falls back to a templated subtext + the raw text on any failure.
    """
    fallback_subtext = (resolution_hint or "Halbot resolved your request").strip()
    if not raw_text:
        return fallback_subtext, raw_text
    pd_block = _format_persona_block()
    if pd_block:
        pd_block = "\n" + pd_block
    system = RICH_CUSTOMIZATION_PROMPT.format(persona_directives_block=pd_block)
    user_msg = f"Original: {raw_text}"
    if resolution_hint:
        user_msg += f"\nResolution: {resolution_hint}"
    if context:
        user_msg += f"\nContext: {context}"
    body = {
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user_msg},
        ],
        "temperature": 0.9,
        "max_tokens": 2000,
        "response_format": {"type": "json_object"},
    }
    _apply_model_and_keepalive(body)
    try:
        resp = requests.post(LLM_URL, json=body, timeout=LLM_TIMEOUT)
        if resp.status_code >= 400:
            if resp.status_code in (400, 404, 409, 503):
                if ensure_model_loaded(LLM_MODEL):
                    resp = requests.post(LLM_URL, json=body, timeout=LLM_TIMEOUT)
        resp.raise_for_status()
        message = resp.json()["choices"][0].get("message", {})
        content = (message.get("content") or "").strip()
        if "<think>" in content and "</think>" in content:
            _, _, rest = content.partition("</think>")
            content = rest.strip()
        # Tolerate models that wrap JSON in fences.
        if content.startswith("```"):
            content = content.strip("`")
            if content.lower().startswith("json"):
                content = content[4:].lstrip()
        parsed = json.loads(content)
        subtext = (parsed.get("subtext") or fallback_subtext).strip()
        out_body = (parsed.get("body") or raw_text).strip()
        log.info("[customize-rich] %r → subtext=%r body=%r",
                 raw_text[:60], subtext[:80], out_body[:120])
        return subtext, out_body
    except Exception as e:
        log.warning("[customize-rich] failed (%s); falling back to single-pass customize", e)
        plain = customize_response(raw_text, context=context)
        return fallback_subtext, plain


async def customize_response_rich_async(raw_text: str, *, context: str = "",
                                        resolution_hint: str = "") -> tuple[str, str]:
    """Async wrapper: runs customize_response_rich in a worker thread."""
    return await asyncio.to_thread(
        customize_response_rich, raw_text,
        context=context, resolution_hint=resolution_hint,
    )


WAKE_VARIANTS_PROMPT = """\
You generate phonetic and Whisper-typical mishearings of a wake word so a
voice bot's substring matcher catches more invocations. Be GENEROUS and
EXHAUSTIVE — false positives are cheap (the bot answers an extra
sentence), false negatives are costly (the user is ignored). Aim wide.

Word: {word}

Brainstorm out loud (mentally) before writing the JSON:
1. Sound the word out by syllables. For each syllable, list common
   Whisper substitutions (e.g. "hal" → "hail", "hall", "howl", "haul",
   "hull", "owl", "al"; "bot" → "bought", "but", "butt", "bod", "pot",
   "boat", "boot", "bought").
2. Combine syllable substitutions: every plausible sub × every other.
   "halbot" → hailbot, howlbot, haulbot, hullbot, halbought, halpot,
   halboat, hailbought, howl bought, hal boat, …
3. Vary spacing and hyphens for each combo: "hal bot", "hal-bot",
   "halbot", "ha lbot". Whisper inserts and drops spaces unpredictably.
4. Add same-rhyme English words/phrases that sound like the whole
   word, even if not literal substitutions: "all bot", "owl bought",
   "how about", "hot pot" — anything that overlaps phonetically with
   what a user said.
5. Add common rhymes ending the word: anything ending in -bot, -bought,
   -bod, -boat, -pot, -but if the back syllable contributes.
6. Include any alternate stress / drawl / accent versions (Southern
   "haaal", British "hawl", clipped "hal'bot").

Then return STRICT JSON: {{"variants": ["...", "...", ...]}}
- 30-60 lowercase items. More is better — go to 60 if syllable
  combinatorics support it.
- Include the word itself as the first item.
- Items can be 1-3 words (substring scan; longer phrases rarely show
  up in one Whisper segment).
- INCLUDE single-word fragments that often appear in Whisper output
  even alone ("howlbot", "hailbot") — substring scan catches them
  inside any larger transcript.
- INCLUDE phrases with a leading/trailing common filler word if it
  changes Whisper's segmentation ("hey halbot", "uh halbot").
- Skip ordinary English words/phrases that have NO phonetic overlap
  with the wake word.
- No duplicates. No explanations. JSON only — no prose, no fences.
"""


def generate_wake_variants(word: str) -> list[str]:
    """Ask the configured LLM for a list of wake-word variants.

    Returns the parsed list on success. Raises on any failure (HTTP,
    JSON parse, schema mismatch, empty list) so the caller can abort
    rather than blow away the existing dictionary.
    """
    word_clean = (word or "").strip().lower()
    if not word_clean:
        raise ValueError("word is empty")
    body = {
        "messages": [
            {"role": "system", "content": WAKE_VARIANTS_PROMPT.format(word=word_clean)},
            {"role": "user", "content": f"Generate variants of {word_clean!r}."},
        ],
        "temperature": 0.7,
        "max_tokens": 2048,
        "response_format": {"type": "json_object"},
    }
    _apply_model_and_keepalive(body)
    resp = requests.post(LLM_URL, json=body, timeout=LLM_TIMEOUT)
    if resp.status_code in (400, 404, 409, 503):
        if ensure_model_loaded(LLM_MODEL):
            resp = requests.post(LLM_URL, json=body, timeout=LLM_TIMEOUT)
    resp.raise_for_status()
    message = resp.json()["choices"][0].get("message", {})
    content = (message.get("content") or "").strip()
    if "<think>" in content and "</think>" in content:
        _, _, rest = content.partition("</think>")
        content = rest.strip()
    if content.startswith("```"):
        content = content.strip("`")
        if content.lower().startswith("json"):
            content = content[4:].lstrip()
    parsed = json.loads(content)
    raw_variants = parsed.get("variants")
    if not isinstance(raw_variants, list) or not raw_variants:
        raise ValueError(f"LLM returned no variants list: {parsed!r}")
    out: list[str] = []
    seen: set[str] = set()

    def _add(token: str) -> None:
        n = token.strip().lower()
        if not n or n in seen:
            return
        seen.add(n)
        out.append(n)

    for v in raw_variants:
        if not isinstance(v, str):
            continue
        n = v.strip().lower()
        if not n:
            continue
        _add(n)
        # Whisper segmentation is unstable: emit the token, the
        # space-stripped form, and the hyphen-stripped form so substring
        # scan catches "howlbot" when the LLM only produced "howl bot".
        compact = "".join(ch for ch in n if not ch.isspace())
        if compact and compact != n:
            _add(compact)
        nohyphen = compact.replace("-", "")
        if nohyphen and nohyphen != compact:
            _add(nohyphen)
    if not out:
        raise ValueError("LLM returned only empty / duplicate variants")
    log.info("[wake-variants] %r → %d items: %r", word_clean, len(out), out[:8])
    return out


async def generate_wake_variants_async(word: str) -> list[str]:
    return await asyncio.to_thread(generate_wake_variants, word)


FLAVOR_SUBTEXT_PROMPT = """\
You are Halbot. Produce ONE short persona-voiced lead-in line that will
render as italic grey subtext above a literal data listing (e.g. a sound
library, persona list, fact list). The listing itself is rendered
verbatim — do NOT summarize, rewrite, or replace the data. Your line
just introduces or colors the listing in your current persona voice.

Rules:
- Exactly one line, plain text, no quotes, no markdown fences, no JSON.
- <= 120 chars. One short sentence or fragment.
- It may reference WHAT the listing is (e.g. "here's the library",
  "every directive you've chained me to"), in your persona voice.
- Do NOT fabricate entries, counts, or names. The data comes from the
  listing below — do not restate it.
- If a persona directive demands a specific verbal tic, honor it within
  the one-line budget.
- Output the line only — nothing else.
{persona_directives_block}
"""


def customize_flavor(resolution_hint: str = "", *, context: str = "") -> str:
    """One-line persona-voiced lead-in for literal listings.

    Used when the body is a verbatim directory dump (library, persona_list,
    fact_list, etc.) that MUST NOT be rewritten, but we still want persona
    flavor in the italic subtext slot. Falls back to resolution_hint on any
    failure.
    """
    fallback = (resolution_hint or "Halbot listing").strip()
    pd_block = _format_persona_block()
    if pd_block:
        pd_block = "\n" + pd_block
    system = FLAVOR_SUBTEXT_PROMPT.format(persona_directives_block=pd_block)
    user_msg = f"Listing kind / resolution: {resolution_hint or 'listing'}"
    if context:
        user_msg += f"\nContext: {context}"
    body = {
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user_msg},
        ],
        "temperature": 0.9,
        "max_tokens": 120,
    }
    _apply_model_and_keepalive(body)
    try:
        resp = requests.post(LLM_URL, json=body, timeout=LLM_TIMEOUT)
        if resp.status_code >= 400:
            if resp.status_code in (400, 404, 409, 503):
                if ensure_model_loaded(LLM_MODEL):
                    resp = requests.post(LLM_URL, json=body, timeout=LLM_TIMEOUT)
        resp.raise_for_status()
        message = resp.json()["choices"][0].get("message", {})
        content = (message.get("content") or "").strip()
        if "<think>" in content and "</think>" in content:
            _, _, rest = content.partition("</think>")
            content = rest.strip()
        if content.startswith("```"):
            content = content.strip("`").strip()
        # Strip surrounding quotes models sometimes add.
        if len(content) >= 2 and content[0] == content[-1] in ('"', "'"):
            content = content[1:-1].strip()
        # Collapse to first non-empty line; hard cap length.
        for line in content.splitlines():
            line = line.strip()
            if line:
                content = line
                break
        if len(content) > 200:
            content = content[:200].rstrip()
        if not content:
            return fallback
        log.info("[customize-flavor] hint=%r → %r", resolution_hint[:60], content[:100])
        return content
    except Exception as e:
        log.warning("[customize-flavor] failed (%s); falling back to hint", e)
        return fallback


async def customize_flavor_async(resolution_hint: str = "", *, context: str = "") -> str:
    return await asyncio.to_thread(customize_flavor, resolution_hint, context=context)


STATS_QUESTION_PROMPT = """\
You are Halbot answering a user's question about your own usage,
analytics, or historical activity in a Discord text channel. You have
access to the raw event log (recent events) plus a rollup of totals
below. Answer the user's question naturally and specifically — cite
numbers, user names, sound names, and dates where relevant. Use Discord
markdown (**bold**, bullet lists, headers) for readability.

CURRENT TIME (UTC): {now_iso}   (unix={now_unix})
Interpret relative dates ("today", "yesterday", "last wednesday",
"this week") relative to this moment in UTC. A "day" is a UTC calendar
day unless the user specifies otherwise.
{persona_directives_block}

KIND GLOSSARY:
- mention: user @-mentioned the bot in text. target = "mention" or "reply".
- cmd_invoke: bot parsed and dispatched an action. target = action name
  (list, save, remove, stats, voice_join, voice_play, persona_set, etc.).
- soundboard_play: a sound was played in voice. target = sound name.
  meta may include {{source: live|saved, bytes: N}}.
- voice_join / voice_leave: bot joined/left a voice channel. target = channel id or name.
- llm_call: LLM request. target labels the call site (parse_intent, parse_voice_command, etc.).
  meta.latency_ms, meta.status.
- tts_request: TTS synthesis. meta.latency_ms, meta.chars, meta.voice.
- wake_word_detected: wake word heard in voice.

DASHBOARD ROLLUP (pre-computed totals):
{rollup_block}

RECENT EVENTS (most recent first; format: `ts_iso | kind | user | target | meta`):
{events_block}

RULES:
- Answer in 1 concise message; under ~350 words unless user explicitly
  asked for a full dump.
- Do NOT invent numbers. If the data does not answer the question,
  say so and suggest what IS available.
- If the user asks a "top N" / "most" / "who" style question, COUNT the
  relevant events and give the actual ranking with numbers.
- If the question is time-scoped (e.g. "last wednesday"), filter events
  to that UTC date range before counting.
- If the result list is long, show top 10 and say "…and N more".
- Refer to users by their display name as shown in the event log.
- Reply with the answer text only — no JSON, no code fences around the
  whole reply.
"""


def _iso_utc(ts_unix: int) -> str:
    from datetime import datetime, timezone
    try:
        return datetime.fromtimestamp(int(ts_unix), tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
    except Exception:
        return str(ts_unix)


def _compact_meta(meta: dict) -> str:
    if not isinstance(meta, dict) or not meta:
        return ""
    # Keep only the informative fields; drop verbose ones.
    keep = {}
    for k in ("source", "latency_ms", "status", "bytes", "chars", "voice",
              "action_count", "channel", "channel_id", "duration_ms",
              "tokens_in", "tokens_out", "reason"):
        if k in meta and meta[k] is not None:
            keep[k] = meta[k]
    if not keep:
        # Fall back to the first 2 keys for novel event shapes.
        items = list(meta.items())[:2]
        keep = dict(items)
    return ",".join(f"{k}={v}" for k, v in keep.items())


def format_events_for_prompt(events: list[dict],
                             uid_to_name: dict[int, str]) -> str:
    """Render event rows for STATS_QUESTION_PROMPT. Compact, one line each."""
    if not events:
        return "(no events in window)"
    lines = []
    for e in events:
        uid = e.get("user_id") or 0
        user = uid_to_name.get(uid) if uid else ""
        if not user:
            user = f"user_{uid}" if uid else "-"
        meta_str = _compact_meta(e.get("meta") or {})
        lines.append(
            f"{_iso_utc(e['ts'])} | {e['kind']} | {user} | "
            f"{e.get('target', '')} | {meta_str}"
        )
    return "\n".join(lines)


def answer_stats_question(question: str, *, rollup_block: str,
                          events_block: str, now_unix: int) -> str:
    """Free-form LLM answer to a user's stats question. Persona-aware."""
    pd_block = _format_persona_block(
        header="ACTIVE BEHAVIOR DIRECTIVES (shape tone + phrasing of your answer)"
    )
    if pd_block:
        pd_block = "\n" + pd_block
    system = STATS_QUESTION_PROMPT.format(
        now_iso=_iso_utc(now_unix),
        now_unix=now_unix,
        persona_directives_block=pd_block,
        rollup_block=rollup_block or "(unavailable)",
        events_block=events_block or "(no events)",
    )
    user_msg = (question or "").strip() or "Give me a summary of recent bot activity."
    body = {
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user_msg},
        ],
        "temperature": 0.7,
        "max_tokens": 3600,
    }
    _apply_model_and_keepalive(body)
    try:
        resp = requests.post(LLM_URL, json=body, timeout=LLM_RETRY_TIMEOUT)
        if resp.status_code >= 400:
            if resp.status_code in (400, 404, 409, 503):
                if ensure_model_loaded(LLM_MODEL):
                    resp = requests.post(LLM_URL, json=body, timeout=LLM_RETRY_TIMEOUT)
        resp.raise_for_status()
        message = resp.json()["choices"][0].get("message", {})
        content = (message.get("content") or "").strip()
        if "<think>" in content and "</think>" in content:
            _, _, rest = content.partition("</think>")
            content = rest.strip()
        elif "</think>" in content:
            _, _, rest = content.partition("</think>")
            content = rest.strip()
        if not content:
            reasoning = (message.get("reasoning_content") or message.get("reasoning") or "").strip()
            if reasoning:
                content = reasoning
        if not content:
            # Last-resort: hand the user the raw rollup so they get SOMETHING
            # instead of a stub. The rollup is already persona-neutral and
            # legible.
            log.warning("[stats-qa] empty LLM output; returning rollup fallback")
            return "Couldn't compose a narrative answer, so here's the raw rollup:\n\n" + (rollup_block or "(no data)")
        log.info("[stats-qa] q=%r → %d chars", user_msg[:80], len(content))
        return content
    except requests.ConnectionError:
        log.error("Stats QA: could not connect to Ollama at %s", LLM_URL)
        return "Couldn't reach Ollama to analyze stats — is it running?"
    except Exception as e:
        log.warning("[stats-qa] failed: %s", e)
        return f"Stats analysis failed ({type(e).__name__})."


async def answer_stats_question_async(question: str, *, rollup_block: str,
                                      events_block: str, now_unix: int) -> str:
    return await asyncio.to_thread(
        answer_stats_question,
        question,
        rollup_block=rollup_block,
        events_block=events_block,
        now_unix=now_unix,
    )


VOICE_CONVERSATION_CODA = """\

VOICE-CHAT OUTPUT OVERRIDE — the speaker is in a voice channel and your
reply will be TTS'd and played aloud. Regardless of any other action
you'd normally emit, respond with EXACTLY ONE JSON object:

  {"action": "reply", "message": "<your spoken reply>"}

Message rules (these are TTS'd — follow them strictly):
- 1 to 2 sentences. Hard cap 60 words.
- Plain spoken English only — no markdown, no asterisks, no bullet
  lists, no code blocks, no URLs, no emoji, no stage directions in
  brackets, no JSON inside the message.
- Speak directly to the user. Do not repeat their question verbatim.
- Respect ALL ACTIVE BEHAVIOR DIRECTIVES and PERSONA STACKING rules
  above — they apply to the message text.
- If you don't know something, say so briefly rather than inventing.
- Do NOT say "as an AI" or similar disclaimers.

Reply with ONLY the JSON object, no prose, no markdown fences.
"""


def answer_voice_conversation(
    command: str,
    sounds=None,
    saved: list[dict] | None = None,
    history: list[dict] | None = None,
    guild=None,
    voice_channel_name: str | None = None,
    _stats_out: dict | None = None,
) -> str:
    """Generate a conversational spoken reply using the SAME model and prompt
    pipeline as text. Slow but full-context: personas stack, emoji list +
    sound list present, voice status injected. Returns plain-text TTS-ready.
    """
    from datetime import date

    emoji_records = emoji_db_list()
    if emoji_records:
        emoji_lines = []
        for e in emoji_records:
            prefix = "a" if e["animated"] else ""
            desc = f" — {e['description']}" if e.get("description") else ""
            emoji_lines.append(f"- {e['name']} → <{prefix}:{e['name']}:{e['emoji_id']}>{desc}")
        emojis_str = "\n".join(emoji_lines)
    else:
        emojis_str = "(none)"

    personas = persona_list()
    if personas:
        persona_lines = [f"- [id:{p['id']}] {p['directive']} (set by {p['set_by']})" for p in personas]
        persona_str = "\n".join(persona_lines) + "\n\n" + PERSONA_STACKING_GUIDE
    else:
        persona_str = "(none — use your default personality)"

    vc_name = voice_channel_name or "a voice channel"
    voice_status_str = (
        f"Currently connected to voice channel '{vc_name}' and actively "
        f"listening. The speaker is IN that voice channel right now. "
        f"Your reply WILL be spoken aloud via TTS."
    )
    voice_channels_str = f"- {vc_name} (currently joined)"

    system_base = SYSTEM_PROMPT.format(
        sound_details=format_sound_details(sounds or []),
        saved_details=format_saved_details(saved or []),
        custom_emojis=emojis_str,
        persona_directives=persona_str,
        facts_block=_format_facts_block(),
        grudges_block=_format_grudges_block(),
        triggers_block=_format_triggers_block(),
        today=date.today().isoformat(),
        attachments="No attachments on this message.",
        voice_channels=voice_channels_str,
        voice_status=voice_status_str,
    )
    system = system_base + VOICE_CONVERSATION_CODA

    chat_history = _voice_history_messages(history)

    body = {
        "messages": [
            {"role": "system", "content": system},
            *chat_history,
            {"role": "user", "content": command or ""},
        ],
        "temperature": 0.8,
        # Voice reply = 1-2 spoken sentences, ~100 tokens real output.
        # Was 4096 which let gemma4 reason for a full minute before
        # speaking. 512 caps total generation while still covering
        # reasoning + answer per our ollama direct tests.
        "max_tokens": 512,
        "think": False,
        "reasoning_effort": "none",
    }
    _apply_model_and_keepalive(body)

    try:
        resp = requests.post(LLM_URL, json=body, timeout=LLM_RETRY_TIMEOUT)
        if resp.status_code >= 400:
            log.warning("[voice-convo] %s: %s", resp.status_code, resp.text[:300])
            if resp.status_code in (400, 404, 409, 503):
                if ensure_model_loaded(LLM_MODEL):
                    resp = requests.post(LLM_URL, json=body, timeout=LLM_RETRY_TIMEOUT)
        resp.raise_for_status()
        raw_json = resp.json()
        usage = raw_json.get("usage", {})
        if _stats_out is not None:
            _stats_out["prompt_tokens"] = int(usage.get("prompt_tokens") or 0)
            _stats_out["completion_tokens"] = int(usage.get("completion_tokens") or 0)
            _stats_out["outcome"] = "ok"
        message = raw_json["choices"][0].get("message", {})
        content = (message.get("content") or "").strip()
        if "</think>" in content:
            _, _, rest = content.partition("</think>")
            content = rest.strip()
        if not content:
            reasoning = (message.get("reasoning_content") or message.get("reasoning") or "").strip()
            if reasoning:
                start = reasoning.find("{")
                end = reasoning.rfind("}")
                if start != -1 and end > start:
                    content = reasoning[start:end + 1]
        if content.startswith("```"):
            content = content.split("\n", 1)[1] if "\n" in content else content[3:]
            if content.endswith("```"):
                content = content[:-3]
            content = content.strip()
        reply_text = ""
        try:
            parsed = json.loads(content)
            if isinstance(parsed, list) and parsed:
                parsed = parsed[0]
            if isinstance(parsed, dict):
                reply_text = str(parsed.get("message") or parsed.get("reply") or "").strip()
        except json.JSONDecodeError:
            # Model ignored JSON wrapper — take raw content as spoken reply.
            log.info("[voice-convo] non-JSON response, using raw content")
            reply_text = content
        # Strip surrounding quotes
        if len(reply_text) >= 2 and reply_text[0] == reply_text[-1] in ('"', "'"):
            reply_text = reply_text[1:-1].strip()
        # Defensive markdown strip
        reply_text = reply_text.replace("**", "").replace("__", "").replace("`", "")
        if not reply_text:
            return "Hmm, I blanked on that one — try me again?"
        log.info("[voice-convo] → %r", reply_text[:140])
        return reply_text
    except requests.exceptions.Timeout:
        log.error("[voice-convo] timed out")
        if _stats_out is not None:
            _stats_out["outcome"] = "timeout"
        return "I took too long thinking — try again?"
    except requests.ConnectionError:
        log.error("[voice-convo] could not connect to Ollama at %s", LLM_URL)
        if _stats_out is not None:
            _stats_out["outcome"] = "connect_error"
        return "My brain's offline — Ollama isn't answering."
    except Exception as e:
        log.warning("[voice-convo] failed: %s", e)
        if _stats_out is not None:
            _stats_out["outcome"] = "error"
        return "Sorry, I glitched on that — ask me again?"


async def answer_voice_conversation_async(
    command: str,
    sounds=None,
    saved: list[dict] | None = None,
    history: list[dict] | None = None,
    guild=None,
    voice_channel_name: str | None = None,
    _stats_out: dict | None = None,
) -> str:
    return await asyncio.to_thread(
        answer_voice_conversation, command, sounds, saved, history, guild, voice_channel_name,
        _stats_out,
    )


_PLAYED_SOUND_MARKER = re.compile(r"^\(played sound: (.+)\)\s*$")
_FAILED_SOUND_MARKER = re.compile(r"^\(failed to play: (.+)\)\s*$")


def _normalize_history_response(text: str) -> str:
    """Translate legacy free-text action markers to the JSON form the LLM emits.

    Older voice_history rows store ``(played sound: fah)`` as the bot
    turn. Feeding those back as assistant messages teaches the LLM to
    mimic the marker as a free-text reply on the next play request,
    which the bot then routes through TTS instead of soundboard
    playback. Rewrite to canonical JSON so the LLM only ever sees
    action-shaped assistant turns for plays.
    """
    if not text:
        return text
    m = _PLAYED_SOUND_MARKER.match(text) or _FAILED_SOUND_MARKER.match(text)
    if m:
        return json.dumps({"action": "voice_play", "name": m.group(1).strip()})
    return text


def _voice_history_messages(history: list[dict] | None) -> list[dict]:
    """Turn a voice_history list into OpenAI-chat message dicts."""
    if not history:
        return []
    msgs: list[dict] = []
    for turn in history:
        user_text = f"{turn['user_display_name']}: {turn['transcript']}"
        msgs.append({"role": "user", "content": user_text})
        msgs.append({
            "role": "assistant",
            "content": _normalize_history_response(turn["bot_response"]),
        })
    return msgs


def parse_voice_intent(transcript: str, sounds, saved: list[dict],
                       history: list[dict] | None = None,
                       _stats_out: dict | None = None) -> list[dict]:
    """Lightweight LLM call to pick a sound from a voice command transcript."""
    _t_enter = _time.monotonic()
    pd_block = _format_persona_block()

    system = VOICE_COMMAND_PROMPT.format(
        sound_details=format_sound_details(sounds),
        saved_details=format_saved_details(saved),
        persona_directives_block=pd_block,
    )

    body = {
        "messages": [
            {"role": "system", "content": system},
            *_voice_history_messages(history),
            {"role": "user", "content": transcript},
        ],
        "temperature": 0.1,
        # gemma4:e4b reasons into the `reasoning` channel before answering
        # (verified by hitting ollama directly with the real prompt on
        # 2026-04-22). 128 tokens truncated reasoning + left `content`
        # empty → parse_voice_intent returned no actions → silent failure
        # that looked like a 2-minute hang in the live log. 256 is enough
        # headroom for reasoning + JSON answer across every prompt we
        # tried (~22 tokens of actual answer).
        "max_tokens": 256,
        # Honored by reasoning-capable openai-compat backends. gemma4
        # ignores it but leaving it in case llm_model is swapped.
        "reasoning_effort": "none",
        # Ollama-native disable of the hidden thinking channel. Did cut
        # reasoning to zero on short prompts in local testing; on bigger
        # prompts gemma4 reasons anyway (max_tokens=256 covers that).
        "think": False,
        # Grammar-constrain the sampler to valid JSON. Kills the
        # prose-before-JSON failure mode independently of reasoning.
        "response_format": {"type": "json_object"},
        # Ollama-native json grammar. response_format alone is not
        # honored by gemma4:e2b on the openai-compat path (verified
        # 2026-04-25: model emitted plain "How's it going?" against
        # response_format=json_object). format=json is the harder
        # constraint and ollama applies it on the /v1/ path too.
        "format": "json",
    }
    _apply_model_and_keepalive(body)

    log.info("[voice-llm] stage=http-send build_ms=%d prompt_chars=%d",
             int((_time.monotonic() - _t_enter) * 1000), len(system))
    content = ""
    try:
        _t_http = _time.monotonic()
        resp = requests.post(LLM_URL, json=body, timeout=LLM_TIMEOUT)
        if resp.status_code >= 400:
            log.warning("Voice LLM %s: %s", resp.status_code, resp.text[:300])
            if resp.status_code in (400, 404, 409, 503):
                if ensure_model_loaded(LLM_MODEL):
                    resp = requests.post(LLM_URL, json=body, timeout=LLM_RETRY_TIMEOUT)
        resp.raise_for_status()
        _llm_ms = int((_time.monotonic() - _t_http) * 1000)
        raw_json = resp.json()
        choice = raw_json["choices"][0]
        message = choice.get("message", {})
        finish_reason = choice.get("finish_reason", "unknown")
        usage = raw_json.get("usage", {})
        if _stats_out is not None:
            _stats_out["prompt_tokens"] = int(usage.get("prompt_tokens") or 0)
            _stats_out["completion_tokens"] = int(usage.get("completion_tokens") or 0)
            _stats_out["llm_ms"] = _llm_ms
            _stats_out["outcome"] = "ok"
        content = (message.get("content") or "").strip()
        reasoning = (message.get("reasoning_content") or message.get("reasoning") or "").strip()
        if "</think>" in content:
            before, _, rest = content.partition("</think>")
            content = rest.strip()
            log.debug("[voice-llm] stripped <think> block (%d chars)", len(before))
        if not content and reasoning:
            log.warning("[voice-llm] content empty, falling back to reasoning_content JSON")
            start = reasoning.find("{")
            end = reasoning.rfind("}")
            if start != -1 and end > start:
                content = reasoning[start:end + 1]
        log.info("[voice-llm] finish_reason=%s usage=%s content=%r",
                 finish_reason, usage, content[:200])
        if not content:
            log.error("[voice-llm] empty content (finish_reason=%s) — bump max_tokens or check model", finish_reason)
            return [{"action": "unknown", "message": f"LLM returned no content (finish_reason={finish_reason})."}]
        if content.startswith("```"):
            content = content.split("\n", 1)[1] if "\n" in content else content[3:]
            if content.endswith("```"):
                content = content[:-3]
            content = content.strip()
        parsed = json.loads(content)
        if isinstance(parsed, dict):
            return [parsed]
        if isinstance(parsed, list):
            return parsed
        # format=json can wrap a bare string ("How's it going?") as
        # valid JSON. The model wrote prose, not a tool call — pass
        # it through as the conversation reply rather than throwing
        # it away on a redundant second LLM call.
        spoken = str(parsed).strip()
        log.warning("Voice LLM returned non-object JSON, using as conversation reply: %r",
                    spoken[:200])
        return [{"action": "conversation", "reply": spoken}]
    except json.JSONDecodeError:
        # Model wrote a chatty reply instead of JSON. Use it
        # directly as the conversation reply — re-asking a second
        # LLM rarely produces a better answer to the user's actual
        # question, and the first reply was a sensible response.
        log.warning("Voice LLM returned non-JSON, using as conversation reply: %r",
                    content[:200])
        return [{"action": "conversation", "reply": content.strip()}]
    except requests.exceptions.Timeout:
        log.error("Voice intent parse timed out")
        if _stats_out is not None:
            _stats_out["outcome"] = "timeout"
        return [{"action": "unknown", "message": "Voice LLM timed out."}]
    except requests.ConnectionError:
        log.error("Voice intent parse: ollama unreachable")
        if _stats_out is not None:
            _stats_out["outcome"] = "connect_error"
        return [{"action": "unknown", "message": "Voice LLM unreachable."}]
    except Exception as e:
        log.error("Voice intent parse failed: %s", e)
        if _stats_out is not None:
            _stats_out["outcome"] = "error"
        return [{"action": "unknown", "message": "Couldn't process that voice command."}]
