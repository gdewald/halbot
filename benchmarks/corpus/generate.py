"""Generate the voice corpus via kokoro TTS.

Synthesizes a fixed list of utterances at 24 kHz, resamples to 16 kHz
(what faster-whisper expects), writes mono wavs into
``benchmarks/corpus/voice/``. Committed outputs keep the benchmark
reproducible on a fresh clone.

Not a real human voice — but latency numbers only care about input
shape + sample rate, and the transcripts are readable enough to
eyeball for accuracy sanity.

    uv run python -m benchmarks.corpus.generate
"""
from __future__ import annotations

from pathlib import Path

UTTERANCES: list[tuple[str, str]] = [
    ("short-01", "Halbot, play Big Yoshi."),
    ("short-02", "Halbot, list the sounds."),
    ("medium-01", "Halbot, can you tell me what the weather is going to be like tomorrow?"),
    ("medium-02", "Halbot, play that one clip we were laughing at last night, you know the one."),
    ("long-01", "Halbot, I was just thinking about the time we were hanging out in the voice channel "
                "and someone played an air-horn sound effect right as the movie's dramatic pause hit, "
                "do you remember that clip?"),
    ("wakeonly-01", "Hey halbot."),
    ("nocommand-01", "I was telling you about the game earlier, anyway, what do you think?"),
]


def main() -> int:
    import numpy as np
    import soundfile as sf  # type: ignore
    from scipy.signal import resample_poly  # type: ignore
    from kokoro import KPipeline  # type: ignore

    out_dir = Path(__file__).resolve().parent / "voice"
    out_dir.mkdir(parents=True, exist_ok=True)

    pipe = KPipeline(lang_code="a", device="cpu")
    try:
        for p in pipe.model.parameters():
            p.data = p.data.to("cpu")
    except Exception:
        pass

    for name, text in UTTERANCES:
        chunks: list[np.ndarray] = []
        for _g, _p, audio in pipe(text, voice="af_heart", speed=1.0):
            if hasattr(audio, "detach"):
                audio = audio.detach().cpu().numpy()
            chunks.append(np.asarray(audio, dtype="float32"))
        if not chunks:
            print(f"[skip] {name}: no audio")
            continue
        wav24 = np.concatenate(chunks)
        # 24 kHz -> 16 kHz: gcd(24000, 16000) = 8000, so 2/3 rational.
        wav16 = resample_poly(wav24, 16000, 24000).astype("float32")
        path = out_dir / f"{name}.wav"
        sf.write(path, wav16, 16000, subtype="PCM_16")
        duration = len(wav16) / 16000.0
        print(f"[ok] {path.name}  {duration:.2f}s  ({len(text)} chars)")

    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
