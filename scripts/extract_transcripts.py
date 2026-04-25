"""Extract STT input + LLM reply lines from halbot.log into JSONL.

Reads C:\\ProgramData\\Halbot\\logs\\halbot.log (or the path passed
as argv[1]) and writes _data/transcripts.jsonl in the repo root,
one JSON object per line:

    {"ts": "2026-04-25T10:58:17.459", "role": "user",
     "user_id": "192...", "text": "Hello?"}
    {"ts": "2026-04-25T10:58:18.123", "role": "bot",
     "text": "Hi there.", "raw_action": {"action": "reply", ...}}

Reference dump only — gitignored. For longitudinal analytics use
the separate transcript logger proposed in the followup plan.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

LOG_DEFAULT = r"C:\ProgramData\Halbot\logs\halbot.log"
OUT = Path(__file__).resolve().parent.parent / "_data" / "transcripts.jsonl"

# 2026-04-25 10:58:17,459 INFO  halbot: [voice-cmd] stage=begin user=<id> transcript='...'
RE_VCMD = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3})\s+\S+\s+\S+\s+"
    r"\[voice-cmd\] stage=begin user=(?P<uid>\d+) transcript=(?P<q>['\"])(?P<text>.*?)(?P=q)\s*$"
)
# 2026-04-25 10:58:17,459 INFO  halbot: Ollama content: '{"action":"reply","message":"..."}'
# (also LM Studio content: '...')
RE_LLM = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3})\s+\S+\s+\S+\s+"
    r"(?:Ollama|LM Studio) content:\s*(?P<q>['\"])(?P<payload>.*?)(?P=q)\s*$"
)


def _norm_ts(s: str) -> str:
    # 2026-04-25 10:58:17,459 → 2026-04-25T10:58:17.459
    return s.replace(",", ".").replace(" ", "T")


def _try_parse_action(raw: str) -> tuple[str, dict | None]:
    """Strip code-fence/escape and return (message_text, action_dict)."""
    s = raw
    # Some lines escape quotes inside the single-quoted payload as \'
    s = s.replace("\\'", "'")
    # Strip ```json fences if present
    if s.lstrip().startswith("```"):
        s = s.strip().strip("`")
        if s.lower().startswith("json"):
            s = s[4:].lstrip()
    try:
        d = json.loads(s)
    except Exception:
        return raw, None
    if isinstance(d, dict) and "message" in d:
        return str(d.get("message", "")), d
    return raw, None


def main(log_path: str = LOG_DEFAULT) -> int:
    p = Path(log_path)
    if not p.exists():
        print(f"log not found: {p}", file=sys.stderr)
        return 2
    OUT.parent.mkdir(parents=True, exist_ok=True)

    user_n = bot_n = 0
    with p.open("r", encoding="utf-8", errors="replace") as fin, \
         OUT.open("w", encoding="utf-8") as fout:
        for line in fin:
            m = RE_VCMD.match(line)
            if m:
                rec = {
                    "ts": _norm_ts(m.group("ts")),
                    "role": "user",
                    "user_id": m.group("uid"),
                    "text": m.group("text"),
                }
                fout.write(json.dumps(rec, ensure_ascii=False) + "\n")
                user_n += 1
                continue
            m = RE_LLM.match(line)
            if m:
                payload = m.group("payload")
                msg, parsed = _try_parse_action(payload)
                if not msg:
                    continue
                rec = {
                    "ts": _norm_ts(m.group("ts")),
                    "role": "bot",
                    "text": msg,
                }
                if parsed and parsed.get("action") and parsed["action"] != "reply":
                    rec["action"] = parsed["action"]
                fout.write(json.dumps(rec, ensure_ascii=False) + "\n")
                bot_n += 1

    print(f"wrote {OUT}: {user_n} user lines, {bot_n} bot lines")
    return 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:]))
