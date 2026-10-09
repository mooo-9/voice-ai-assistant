"""
Record Mo saying "Hey Fager", for retraining the wake word on his own voice.

data/hey_fager.onnx was trained only on Windows text-to-speech voices, and on
Mo's voice it scores around 0.4-0.6 against its threshold, or near zero. This
records what the training script needs from him:

  positive/  "Hey Fager" 40 times: normal, quiet, across the room, casual
  negative/  16 phrases that sound close but must not wake it
  noise/     one minute of the room as it usually sounds

Each prompt counts down, records two seconds, and asks again if it heard
nothing. Stop El Fager first — it would wake on these.

Run:  python -X utf8 scripts/record_wake_samples.py
"""
import sys
import time
import wave
from pathlib import Path

import numpy as np

RATE = 16000
CLIP_SEC = 2.0
NOISE_SEC = 60.0
QUIET = 0.02        # a peak below this is nothing heard
DEAD = 0.001        # a room is never this quiet: the mic is muted or blocked

OUT = Path(__file__).resolve().parent.parent / "data" / "wake_samples"

POSITIVE = [
    ("normal", 'Say "Hey Fager" the way you usually do.', 10),
    ("quiet", 'Say "Hey Fager" quietly, as if someone is asleep nearby.', 10),
    ("far", 'Step back across the room and say "Hey Fager".', 10),
    ("casual", 'Say "Hey Fager" fast and casually, like mid-thought.', 10),
]

NEGATIVE = [
    "Hey Father", "Hey Farah", "Hey Faisal", "Hey Fatma", "Hey Farid",
    "Hey fella", "Hey there", "Okay Google", "Hey Siri", "Hey, how are you",
    "Play some music", "What's the weather", "Yes, send it", "Thanks",
    "Ya Fares", "Ya basha",
]


def _record(seconds: float) -> np.ndarray:
    import sounddevice as sd
    audio = sd.rec(int(seconds * RATE), samplerate=RATE, channels=1, dtype="float32")
    sd.wait()
    return audio.flatten()


def _save(path: Path, audio: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pcm = (np.clip(audio, -1, 1) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm.tobytes())


def _countdown(prompt: str) -> None:
    print(f"\n{prompt}")
    for n in (3, 2, 1):
        print(f"  {n}…", end="", flush=True)
        time.sleep(0.6)
    print("  SPEAK NOW", flush=True)


def _take(path: Path, prompt: str, record=_record) -> None:
    """One clip, asked again until something was heard."""
    while True:
        _countdown(prompt)
        audio = record(CLIP_SEC)
        peak = float(np.abs(audio).max()) if audio.size else 0.0
        if peak >= QUIET:
            _save(path, audio)
            print(f"  saved ({peak:.2f})")
            return
        print("  didn't hear that — once more")


def main(out: Path = OUT, positive=POSITIVE, negative=NEGATIVE,
         noise_sec: float = NOISE_SEC, record=_record) -> None:
    room = record(1.0)
    if not room.size or float(np.abs(room).max()) < DEAD:
        print("The microphone is giving silence — check it isn't muted (mute key, "
              "Windows sound settings, or Privacy > Microphone), then run this again.")
        sys.exit(1)

    stamp = time.strftime("%Y%m%d-%H%M%S")
    total = sum(n for _, _, n in positive)
    done = 0
    print(f"Part 1 of 3 — \"Hey Fager\" {total} times.")
    for style, prompt, count in positive:
        for i in range(count):
            done += 1
            _take(out / "positive" / f"{stamp}-{style}-{i:02d}.wav",
                  f"[{done}/{total}] {prompt}", record)

    print(f"\nPart 2 of 3 — {len(negative)} phrases that must NOT wake it.")
    for i, phrase in enumerate(negative):
        _take(out / "negative" / f"{stamp}-phrase-{i:02d}.wav",
              f'[{i + 1}/{len(negative)}] Say: "{phrase}"', record)

    print(f"\nPart 3 of 3 — {noise_sec:.0f} seconds of the room. Don't say anything;"
          " leave music or the TV on if that's usual.")
    time.sleep(2)
    _save(out / "noise" / f"{stamp}-room.wav", record(noise_sec))
    print(f"\nDone. Samples are in {out}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped. What was recorded so far is kept.")
        sys.exit(1)
