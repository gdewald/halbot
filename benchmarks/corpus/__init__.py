"""Benchmark input corpus.

- voice/*.wav   — 16 kHz mono wavs for STT input. Regenerate via
                  ``python -m benchmarks.corpus.generate``.
- prompts/voice_prompts.jsonl — one JSON object per line, either
                  ``{"id": str, "prompt": str}`` for a plain user-message
                  payload, or ``{"id": str, "messages": [...]}`` for a
                  full captured chat body.
- texts/tts_texts.txt — one synth target per line.
"""
