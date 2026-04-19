import io
import os

from pydub import AudioSegment
from tinytag import TinyTag

SOUNDBOARD_MAX_BYTES = 512 * 1024  # 512 KB
SOUNDBOARD_MAX_DURATION = 5.2  # seconds
ALLOWED_CONTENT_TYPES = {"audio/mpeg", "audio/ogg", "audio/wav", "audio/x-wav", "audio/mp3"}
ALLOWED_EXTENSIONS = {".mp3", ".ogg", ".wav"}
SUPPORTED_EFFECTS = {"echo", "reverb", "pitch"}


def validate_audio(data: bytes, filename: str) -> tuple[bool, str, float | None]:
    """Validate audio data for soundboard compatibility.

    Returns (ok, message, duration_seconds).
    """
    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        return False, f"Unsupported format `{ext}`. Must be MP3, OGG, or WAV.", None

    if len(data) > SOUNDBOARD_MAX_BYTES:
        size_kb = round(len(data) / 1024, 1)
        return False, f"File too large ({size_kb}KB). Max is 512KB.", None

    try:
        tag = TinyTag.get(file_obj=io.BytesIO(data))
        duration = tag.duration or 0
    except Exception:
        return False, "Couldn't read audio metadata. Is the file valid?", None

    if duration > SOUNDBOARD_MAX_DURATION:
        return False, f"Too long ({duration:.1f}s). Max is {SOUNDBOARD_MAX_DURATION}s.", duration

    return True, "OK", duration


def detect_audio_format(data: bytes) -> str:
    """Sniff audio format from file header bytes."""
    if data[:3] == b"ID3" or data[:2] == b"\xff\xfb" or data[:2] == b"\xff\xf3":
        return "mp3"
    if data[:4] == b"OggS":
        return "ogg"
    if data[:4] == b"RIFF":
        return "wav"
    return "mp3"  # fallback


def apply_effect(audio_bytes: bytes, fmt: str, effect_type: str, params: dict) -> bytes:
    """Apply a single audio effect. Returns processed audio bytes in the same format."""
    sound = AudioSegment.from_file(io.BytesIO(audio_bytes), format=fmt)

    if effect_type == "echo":
        delay_ms = int(params.get("delay", 300))
        decay_db = float(params.get("decay", 6))
        repeats = int(params.get("repeats", 3))
        result = sound
        for i in range(1, repeats + 1):
            delayed = AudioSegment.silent(duration=delay_ms * i) + (sound - decay_db * i)
            result = result.overlay(delayed)

    elif effect_type == "reverb":
        room_size = max(0.0, min(1.0, float(params.get("room_size", 0.5))))
        num_taps = int(8 + room_size * 20)
        result = sound
        for i in range(1, num_taps + 1):
            tap_delay = int(i * 15 * (1 + room_size))
            tap_decay = 3 * i
            if tap_decay > 40:
                break
            delayed = AudioSegment.silent(duration=tap_delay) + (sound - tap_decay)
            result = result.overlay(delayed)

    elif effect_type == "pitch":
        semitones = float(params.get("semitones", 0))
        rate_change = 2 ** (semitones / 12.0)
        new_rate = int(sound.frame_rate * rate_change)
        pitched = sound._spawn(sound.raw_data, overrides={"frame_rate": new_rate})
        result = pitched.set_frame_rate(sound.frame_rate)

    else:
        raise ValueError(f"Unknown effect: {effect_type}")

    # Truncate to soundboard max duration
    max_ms = int(SOUNDBOARD_MAX_DURATION * 1000)
    if len(result) > max_ms:
        result = result[:max_ms]

    buf = io.BytesIO()
    export_fmt = "mp3" if fmt == "mp3" else fmt
    result.export(buf, format=export_fmt)
    output = buf.getvalue()

    # If output is too large, try re-exporting at lower bitrate
    if len(output) > SOUNDBOARD_MAX_BYTES and fmt == "mp3":
        buf = io.BytesIO()
        result.export(buf, format="mp3", bitrate="128k")
        output = buf.getvalue()

    return output


def apply_effects_chain(original_audio: bytes, effects: list[dict]) -> bytes:
    """Apply a chain of effects sequentially to audio data."""
    fmt = detect_audio_format(original_audio)
    data = original_audio
    for eff in effects:
        data = apply_effect(data, fmt, eff["type"], eff["params"])
    return data
