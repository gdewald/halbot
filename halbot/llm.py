import asyncio
import base64
import json
import logging
import os
from pathlib import Path

import requests

from .db import emoji_db_list, persona_list

log = logging.getLogger("halbot")

LMSTUDIO_URL = os.getenv("LMSTUDIO_URL", "http://localhost:1234/v1/chat/completions")
LMSTUDIO_MODEL = os.getenv("LMSTUDIO_MODEL", "google/gemma-4-e2b")

# Reasoning models can take >30s for a single response. Keep both read
# timeouts generous so a slow generation doesn't surface as "I didn't
# understand that".
LLM_TIMEOUT = 120
LLM_RETRY_TIMEOUT = 180

CHANNEL_HISTORY_LIMIT = 50

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
        base = LMSTUDIO_URL.split("/v1/")[0]
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

Return JSON:
- To play a sound: {{"action": "voice_play", "name": "<exact sound name>"}}
- If no match or the request is unclear: {{"action": "unknown", "message": "<brief response>"}}

Match creatively — "something scary" → pick a scary-sounding name, \
"play airhorn" → exact match. Names must be EXACT from the lists above.

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

WAKE_WORD_PROMPT = """\
You are a wake-word classifier for a Discord bot named "Halbot".

Given a speech transcription (produced by an imperfect STT engine), decide
whether the speaker is addressing Halbot and, if so, extract the command.

Speech-to-text often mis-hears "Halbot" as phonetically similar words.
Treat ALL of these (and any similar mishearing) as a wake word:
  Halbot, Hal Bot, Albot, Owlbot, Palbot, Walbot, Halbert, Hellboy,
  Hellbot, Howlbot, Holbot, Hal-Bot, Hal Bought, How Bout, Al Bought, etc.

The wake word is usually the first word of the utterance but may appear
later ("play big yoshi, halbot"). The COMMAND is everything the speaker
said to the bot MINUS the wake word itself, with leading punctuation
stripped. If the utterance has no command after the wake word (just the
name alone), return command = "".

Reply with ONLY this JSON object, no prose, no markdown:
  {"wake": <true|false>, "command": "<extracted command or empty string>"}

Err on the side of wake=false when the utterance is clearly not directed
at the bot (e.g. general conversation, no phonetic match). Do not invent
a command that was not spoken.
"""

VOICE_COMBINED_PROMPT = """\
You are Halbot, a Discord soundboard bot that listens in a voice channel.
A user spoke and an imperfect STT engine transcribed what they said.  In a
SINGLE response, do two things:

1. Decide whether the speaker addressed you. wake=true if the utterance
   contains the wake word "Halbot" OR any close phonetic mishearing:
   Hal Bot, Albot, Owlbot, Palbot, Walbot, Halbert, Hellboy, Hellbot,
   Howlbot, Holbot, Hal-Bot, Hal Bought, How Bout, Al Bought, etc.
   This decision is PURELY about whether the wake word was spoken — do
   NOT consider whether the command is actionable. If the wake word is
   present, wake=true even if you cannot pick a sound.
2. If wake=true, pick the best sound to play for the command that
   follows the wake word. If no sound fits or there is no command after
   the wake word, return actions=[] (still with wake=true).
3. If wake=false, actions MUST be [].

SAVED LIBRARY:
<<SAVED_DETAILS>>

LIVE SOUNDBOARD:
<<SOUND_DETAILS>>

<<PERSONA_DIRECTIVES>>

Reply with ONLY this JSON, no prose, no markdown:
  {"wake": <true|false>, "actions": [<action>, ...]}

Each action is one of:
  {"action": "voice_play", "name": "<exact sound name>"}
  {"action": "unknown", "message": "<brief response>"}

Match creatively — "something scary" → pick a scary-sounding name,
"play airhorn" → exact match.  Names must be EXACT from the lists above.
"""


def _lmstudio_base() -> str:
    """Strip the OpenAI path suffix to get the LM Studio server root."""
    for marker in ("/v1/", "/api/"):
        idx = LMSTUDIO_URL.find(marker)
        if idx != -1:
            return LMSTUDIO_URL[:idx]
    return LMSTUDIO_URL.rstrip("/")


def ensure_model_loaded(model: str = LMSTUDIO_MODEL, timeout: int = 180) -> bool:
    """Make sure model is loaded in LM Studio, triggering a JIT load if not."""
    base = _lmstudio_base()
    try:
        resp = requests.get(f"{base}/api/v0/models", timeout=5)
        resp.raise_for_status()
        entries = resp.json().get("data", []) or []
        match = next(
            (m for m in entries if model in (m.get("id"), m.get("model_key"))),
            None,
        )
        if match and match.get("state") == "loaded":
            return True
        state = match.get("state") if match else "unknown"
        log.info("Model %r not loaded (state=%s) — triggering JIT load", model, state)
    except requests.RequestException as e:
        log.warning("Could not query LM Studio model state: %s — will try a direct load", e)

    try:
        resp = requests.post(
            LMSTUDIO_URL,
            json={
                "model": model,
                "messages": [{"role": "user", "content": "ping"}],
                "max_tokens": 1,
            },
            timeout=timeout,
        )
        resp.raise_for_status()
        log.info("JIT load completed for %r", model)
        return True
    except requests.RequestException as e:
        log.error("Failed to load model %r: %s", model, e)
        return False


def describe_emoji_image(image_bytes: bytes, name: str) -> str:
    """Send an emoji image to LM Studio vision and get a short description."""
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
    if LMSTUDIO_MODEL:
        body["model"] = LMSTUDIO_MODEL
    try:
        resp = requests.post(LMSTUDIO_URL, json=body, timeout=LLM_TIMEOUT)
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        log.warning("Failed to describe emoji %s: %s", name, e)
        return ""


def format_sound_details(sounds) -> str:
    """Build a detailed listing of live sounds for the LM Studio prompt."""
    if not sounds:
        return "(none)"
    lines = []
    for s in sounds:
        user_name = str(s.user) if s.user else "unknown"
        date_str = s.created_at.strftime("%Y-%m-%d") if s.created_at else "unknown"
        lines.append(f"- \"{s.name}\" (added by {user_name} on {date_str})")
    return "\n".join(lines)


def format_saved_details(saved: list[dict]) -> str:
    """Build a detailed listing of saved sounds for the LM Studio prompt."""
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
                  voice_status_str: str = "Not connected to any voice channel.") -> list[dict]:
    """Send user text + context to LM Studio, return a list of actions to execute."""
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
        persona_str = "\n".join(persona_lines)
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
        "max_tokens": 1536,
    }
    if LMSTUDIO_MODEL:
        body["model"] = LMSTUDIO_MODEL

    try:
        resp = requests.post(LMSTUDIO_URL, json=body, timeout=LLM_TIMEOUT)
        if resp.status_code >= 400:
            log.warning("LM Studio %s response: %s", resp.status_code, resp.text[:500])
            if resp.status_code in (400, 404, 409, 503):
                if ensure_model_loaded(LMSTUDIO_MODEL):
                    log.info("Retrying chat completion after model load")
                    resp = requests.post(LMSTUDIO_URL, json=body, timeout=LLM_RETRY_TIMEOUT)
                    if resp.status_code >= 400:
                        log.warning("LM Studio retry %s response: %s",
                                    resp.status_code, resp.text[:500])
        resp.raise_for_status()
        raw_json = resp.json()
        log.debug("LM Studio raw response: %s", json.dumps(raw_json, indent=2))
        choice = raw_json["choices"][0]
        finish_reason = choice.get("finish_reason", "unknown")
        usage = raw_json.get("usage", {})
        log.info("LM Studio finish_reason=%s, usage=%s", finish_reason, usage)
        content = (choice["message"].get("content") or "").strip()
        log.info("LM Studio content: %r", content)
        if not content:
            log.error("LM Studio returned empty content (finish_reason=%s)", finish_reason)
            return [{"action": "error", "message": "LM Studio returned an empty response — the prompt may be too long for the model's context window."}]
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
                log.warning("LM Studio mimicked BOT REPLY prefix, dropping: %s", content[:200])
                return [{"action": "error", "message": "LLM returned a mimicked prior reply instead of a JSON action. Try again."}]
            log.warning("LM Studio returned non-JSON, treating as unknown: %s", content[:200])
            return [{"action": "unknown", "message": content}]
        log.info("Parsed actions: %s", json.dumps(parsed, indent=2))
        if isinstance(parsed, dict):
            return [parsed]
        if isinstance(parsed, list):
            return parsed
        return [{"action": "unknown", "message": str(parsed)}]
    except requests.ConnectionError:
        log.error("Could not connect to LM Studio at %s", LMSTUDIO_URL)
        return [{"action": "error", "message": "I'm having trouble thinking right now — is LM Studio running?"}]
    except (requests.RequestException, KeyError, IndexError) as e:
        log.error("Failed to parse intent: %s", e)
        return [{"action": "error", "message": f"LM Studio call failed ({type(e).__name__}: {e}). Check LM Studio server."}]


def customize_response(raw_text: str, *, context: str = "") -> str:
    """Rewrite a plain-text response via LM Studio so it matches the active persona.

    Falls back to raw_text on any failure.
    """
    if not raw_text:
        return raw_text
    personas = persona_list()
    if personas:
        pd_block = "\nACTIVE BEHAVIOR DIRECTIVES:\n" + "\n".join(
            f"- {p['directive']}" for p in personas
        )
    else:
        pd_block = ""
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
        "max_tokens": 400,
    }
    if LMSTUDIO_MODEL:
        body["model"] = LMSTUDIO_MODEL
    try:
        resp = requests.post(LMSTUDIO_URL, json=body, timeout=LLM_TIMEOUT)
        if resp.status_code >= 400:
            if resp.status_code in (400, 404, 409, 503):
                if ensure_model_loaded(LMSTUDIO_MODEL):
                    resp = requests.post(LMSTUDIO_URL, json=body, timeout=LLM_TIMEOUT)
        resp.raise_for_status()
        message = resp.json()["choices"][0].get("message", {})
        content = (message.get("content") or "").strip()
        # Handle <think>…</think> wrappers
        if "<think>" in content and "</think>" in content:
            _, _, rest = content.partition("</think>")
            content = rest.strip()
        # Fall back to reasoning_content tail if content got truncated
        if not content:
            reasoning = (message.get("reasoning_content") or "").strip()
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


def check_wake_word(transcript: str) -> tuple[bool, str]:
    """Classify a transcription as a wake-word call and extract the command.

    Returns (wake_detected, command). On any failure returns (False, "").
    """
    body = {
        "messages": [
            {"role": "system", "content": WAKE_WORD_PROMPT},
            {"role": "user", "content": transcript},
        ],
        "temperature": 0.0,
        "max_tokens": 128,
    }
    if LMSTUDIO_MODEL:
        body["model"] = LMSTUDIO_MODEL
    try:
        resp = requests.post(LMSTUDIO_URL, json=body, timeout=LLM_TIMEOUT)
        if resp.status_code >= 400:
            if resp.status_code in (400, 404, 409, 503):
                if ensure_model_loaded(LMSTUDIO_MODEL):
                    resp = requests.post(LMSTUDIO_URL, json=body, timeout=LLM_TIMEOUT)
        resp.raise_for_status()
        content = (resp.json()["choices"][0]["message"].get("content") or "").strip()
        if content.startswith("```"):
            content = content.split("\n", 1)[1] if "\n" in content else content[3:]
            if content.endswith("```"):
                content = content[:-3]
            content = content.strip()
        parsed = json.loads(content)
        wake = bool(parsed.get("wake", False))
        command = str(parsed.get("command", "") or "").strip()
        log.info("[wake-llm] transcript=%r → wake=%s command=%r", transcript, wake, command)
        return wake, command
    except Exception as e:
        log.warning("[wake-llm] classifier failed (%s); ignoring utterance", e)
        return False, ""


def _voice_history_messages(history: list[dict] | None) -> list[dict]:
    """Turn a voice_history list into OpenAI-chat message dicts."""
    if not history:
        return []
    msgs: list[dict] = []
    for turn in history:
        user_text = f"{turn['user_display_name']}: {turn['transcript']}"
        msgs.append({"role": "user", "content": user_text})
        msgs.append({"role": "assistant", "content": turn["bot_response"]})
    return msgs


def parse_voice_combined(
    transcript: str, sounds, saved: list[dict],
    history: list[dict] | None = None,
) -> tuple[str, list[dict]]:
    """Single-call wake detection + intent parsing.

    Returns (status, actions) where status is "wake", "no_wake", or "error".
    """
    personas = persona_list()
    if personas:
        persona_lines = [f"- {p['directive']}" for p in personas]
        pd_block = "ACTIVE BEHAVIOR DIRECTIVES:\n" + "\n".join(persona_lines)
    else:
        pd_block = ""
    system = (
        VOICE_COMBINED_PROMPT
        .replace("<<SAVED_DETAILS>>", format_saved_details(saved))
        .replace("<<SOUND_DETAILS>>", format_sound_details(sounds))
        .replace("<<PERSONA_DIRECTIVES>>", pd_block)
    )
    body = {
        "messages": [
            {"role": "system", "content": system},
            *_voice_history_messages(history),
            {"role": "user", "content": transcript},
        ],
        "temperature": 0.1,
        "max_tokens": 1024,
    }
    if LMSTUDIO_MODEL:
        body["model"] = LMSTUDIO_MODEL

    content = ""
    try:
        resp = requests.post(LMSTUDIO_URL, json=body, timeout=LLM_TIMEOUT)
        if resp.status_code >= 400:
            log.warning("Voice combined LLM %s: %s", resp.status_code, resp.text[:300])
            if resp.status_code in (400, 404, 409, 503):
                if ensure_model_loaded(LMSTUDIO_MODEL):
                    resp = requests.post(LMSTUDIO_URL, json=body, timeout=LLM_RETRY_TIMEOUT)
        resp.raise_for_status()
        raw_json = resp.json()
        choice = raw_json["choices"][0]
        message = choice.get("message", {})
        finish_reason = choice.get("finish_reason", "unknown")
        usage = raw_json.get("usage", {})
        content = (message.get("content") or "").strip()
        reasoning = (message.get("reasoning_content") or "").strip()
        # Some servers leak only the closing </think> even when thinking is disabled mid-stream
        if "</think>" in content:
            _, _, rest = content.partition("</think>")
            content = rest.strip()
        if not content and reasoning:
            start = reasoning.find("{")
            end = reasoning.rfind("}")
            if start != -1 and end > start:
                content = reasoning[start:end + 1]
        log.info("[voice-combined] finish_reason=%s usage=%s content=%r",
                 finish_reason, usage, content[:200])
        if not content:
            return "error", []
        if content.startswith("```"):
            content = content.split("\n", 1)[1] if "\n" in content else content[3:]
            if content.endswith("```"):
                content = content[:-3]
            content = content.strip()
        parsed = json.loads(content)
        wake = bool(parsed.get("wake", False))
        actions = parsed.get("actions") or []
        if not isinstance(actions, list):
            actions = [actions] if isinstance(actions, dict) else []
        return ("wake" if wake else "no_wake"), actions
    except json.JSONDecodeError:
        log.warning("[voice-combined] non-JSON response: %r", content[:200])
        return "error", []
    except Exception as e:
        log.warning("[voice-combined] call failed (%s)", e)
        return "error", []


def parse_voice_intent(transcript: str, sounds, saved: list[dict],
                       history: list[dict] | None = None) -> list[dict]:
    """Lightweight LLM call to pick a sound from a voice command transcript."""
    personas = persona_list()
    if personas:
        persona_lines = [f"- {p['directive']}" for p in personas]
        pd_block = "ACTIVE BEHAVIOR DIRECTIVES:\n" + "\n".join(persona_lines)
    else:
        pd_block = ""

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
        # Bumped from 256 — reasoning-capable models emit <think>…</think>
        # tokens that eat into the budget before the JSON answer is produced.
        "max_tokens": 1024,
    }
    if LMSTUDIO_MODEL:
        body["model"] = LMSTUDIO_MODEL

    content = ""
    try:
        resp = requests.post(LMSTUDIO_URL, json=body, timeout=LLM_TIMEOUT)
        if resp.status_code >= 400:
            log.warning("Voice LLM %s: %s", resp.status_code, resp.text[:300])
            if resp.status_code in (400, 404, 409, 503):
                if ensure_model_loaded(LMSTUDIO_MODEL):
                    resp = requests.post(LMSTUDIO_URL, json=body, timeout=LLM_RETRY_TIMEOUT)
        resp.raise_for_status()
        raw_json = resp.json()
        choice = raw_json["choices"][0]
        message = choice.get("message", {})
        finish_reason = choice.get("finish_reason", "unknown")
        usage = raw_json.get("usage", {})
        content = (message.get("content") or "").strip()
        reasoning = (message.get("reasoning_content") or "").strip()
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
        return [{"action": "unknown", "message": str(parsed)}]
    except json.JSONDecodeError:
        log.warning("Voice LLM returned non-JSON: %r", content[:200])
        return [{"action": "unknown", "message": content or "(empty LLM response)"}]
    except Exception as e:
        log.error("Voice intent parse failed: %s", e)
        return [{"action": "unknown", "message": "Couldn't process that voice command."}]
