"""
Train a custom "Hey Fager" wake word model for El Fager.

Pipeline:
  1. Generate TTS audio of "Hey Fager" (positive) and other phrases (negative)
     using pyttsx3 Windows SAPI voices (no ffmpeg needed)
  2. Resample everything to 16kHz mono float32
  3. Stream each clip 80 ms at a time through openwakeword's feature
     extractor, as the running app does
  4. Build training samples: the 16-frame window as a positive ends, and
     every window along a negative
  5. Train sklearn LogisticRegression
  6. Export as ONNX (input [1,16,96] → output [1,1]) matching openwakeword format
  7. Save to data/hey_fager.candidate.onnx

Trained on those voices alone, the model scored Mo's own "Hey Fager" around
its threshold or near zero. So Mo's recordings (scripts/record_wake_samples.py,
in data/wake_samples/) join the training, counted three times a robot voice's.
Every fourth recording is held back, and the old and new models are scored on
those the way the running app scores sound. The new model only replaces the
running one when asked, and the old one is backed up first.

Run:  python -X utf8 scripts/train_hey_fager.py            # train and compare
      python -X utf8 scripts/train_hey_fager.py --install  # use the new model
"""

import glob, os, shutil, statistics, sys, time, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import numpy as np
import scipy.signal
import scipy.io.wavfile as wavfile
import pyttsx3
import onnx
from onnx import helper, TensorProto, numpy_helper
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import classification_report
from openwakeword.model import Model
from openwakeword.utils import AudioFeatures

TARGET_RATE = 16000
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
OUTPUT_PATH = os.path.join(DATA_DIR, "hey_fager.onnx")
CANDIDATE_PATH = os.path.join(DATA_DIR, "hey_fager.candidate.onnx")
SAMPLES_DIR = os.path.join(DATA_DIR, "wake_samples")

REAL_WEIGHT = 3.0        # Mo's voice is the one that has to wake it
HELD_OUT_EVERY = 4       # every fourth recording is kept back to test on
THRESHOLDS = (0.35, 0.5)
CHUNK = 1280             # 80 ms, as core/wake_word.py feeds the model
STREAM_LEAD_SEC = 2.0    # silence to flush a fresh extractor's random start


# ─── Audio generation ─────────────────────────────────────────────────────────

def _voices() -> list[str]:
    engine = pyttsx3.init()
    return [v.id for v in engine.getProperty("voices")
            if "en" in v.id.lower() or "english" in (v.name or "").lower()]


def _tts_to_wav(text: str, voice_id: str, rate: int = 150) -> np.ndarray | None:
    """Render text via pyttsx3 → resample to 16kHz float32 numpy array."""
    engine = pyttsx3.init()
    engine.setProperty("voice", voice_id)
    engine.setProperty("rate", rate)

    tmp = tempfile.mktemp(suffix=".wav")
    try:
        engine.save_to_file(text, tmp)
        engine.runAndWait()
        time.sleep(0.3)  # pyttsx3 can be async on Windows

        if not os.path.exists(tmp) or os.path.getsize(tmp) < 1000:
            return None

        orig_rate, data = wavfile.read(tmp)

        # Convert to float32 mono
        if data.ndim > 1:
            data = data.mean(axis=1)
        data = data.astype(np.float32)
        if data.dtype == np.float32 and data.max() > 1.0:
            data /= 32768.0
        elif data.max() > 1.0:
            data /= 32768.0

        # Resample to 16kHz
        if orig_rate != TARGET_RATE:
            gcd = np.gcd(TARGET_RATE, orig_rate)
            data = scipy.signal.resample_poly(data, TARGET_RATE // gcd, orig_rate // gcd)

        return data.astype(np.float32)

    except Exception as e:
        print(f"  [TTS error] {text!r} @ {voice_id}: {e}")
        return None
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def generate_samples() -> tuple[list[np.ndarray], list[np.ndarray]]:
    voices = _voices()
    if not voices:
        raise RuntimeError("No English voices found in pyttsx3. Check Windows TTS settings.")
    print(f"[Train] Found {len(voices)} voice(s).")

    POSITIVE = [
        # Hey Fager variants
        "Hey Fager", "hey fager", "Hey, Fager", "HEY FAGER",
        "Hey Fager!", "okay Fager", "Hey Fager are you there",
        "Hey Fager help me", "Hey Fager open Chrome",
        # Wake up Fager variants
        "Wake up Fager", "wake up fager", "Wake Up Fager",
        "wake up fager please", "Fager wake up",
        # Wake up Jarvis variants
        "Wake up Jarvis", "wake up jarvis", "Wake Up Jarvis",
        "wake up jarvis please", "Jarvis wake up",
    ]
    NEGATIVE = [
        "What time is it", "Open Chrome", "Set a reminder",
        "Hey Google what is the weather", "Hello there",
        "Hey Siri play music", "Alexa turn on the light",
        "OK Google search for news", "Tell me a joke",
        "What is on my calendar", "Good morning",
        "Turn off the lights", "Hey Cortana",
        "Search for restaurants nearby", "Call mom",
        "Play some music", "Read my emails",
        "How is the weather today", "Navigate home",
        "Set an alarm for seven AM",
        # Near misses. Without them the model woke on "Hey Father" as surely
        # as on "Hey Fager": nothing had taught it the difference.
        "Hey Father", "Hey Farah", "Hey Faisal", "Hey Fatma", "Hey Farid",
        "Hey fella", "Hey there", "Hey Fred", "Hey Pager", "Hey Tiger",
        "Fager", "Hey",
    ]

    rates = [130, 150, 170]

    pos_clips, neg_clips = [], []

    print("[Train] Generating POSITIVE samples...")
    for phrase in POSITIVE:
        for vid in voices:
            for rate in rates:
                clip = _tts_to_wav(phrase, vid, rate)
                if clip is not None and len(clip) > TARGET_RATE * 0.2:
                    # Pad with 0.3s silence on both sides (simulates real conditions)
                    pad = np.zeros(int(TARGET_RATE * 0.3), dtype=np.float32)
                    pos_clips.append(np.concatenate([pad, clip, pad]))
                    sys.stdout.write(".")
                    sys.stdout.flush()
    print(f"\n[Train] {len(pos_clips)} positive clips")

    print("[Train] Generating NEGATIVE samples...")
    for phrase in NEGATIVE:
        for vid in voices:
            clip = _tts_to_wav(phrase, vid, 150)
            if clip is not None and len(clip) > TARGET_RATE * 0.2:
                # Padded exactly like the positives. Unpadded, "speech then a
                # short silence" was the difference the model learned, and it
                # woke on any phrase followed by a pause.
                pad = np.zeros(int(TARGET_RATE * 0.3), dtype=np.float32)
                neg_clips.append(np.concatenate([pad, clip, pad]))
                sys.stdout.write(".")
                sys.stdout.flush()
    # Also add pure silence clips as negatives
    for _ in range(10):
        neg_clips.append(np.zeros(TARGET_RATE * 2, dtype=np.float32))
        # (silence is float32 zeros — will be converted to int16 in extract_features)
    print(f"\n[Train] {len(neg_clips)} negative clips")

    return pos_clips, neg_clips


# ─── Mo's recordings ──────────────────────────────────────────────────────────

def read_wav(path) -> np.ndarray:
    """A recording as 16 kHz mono float32."""
    rate, data = wavfile.read(str(path))
    if data.ndim > 1:
        data = data.mean(axis=1)
    data = data.astype(np.float32)
    if np.abs(data).max(initial=0) > 1.0:
        data /= 32768.0
    if rate != TARGET_RATE:
        gcd = np.gcd(TARGET_RATE, rate)
        data = scipy.signal.resample_poly(data, TARGET_RATE // gcd, rate // gcd)
    return data.astype(np.float32)


def load_recorded(root=SAMPLES_DIR) -> dict:
    return {kind: [read_wav(p) for p in sorted(glob.glob(os.path.join(str(root), kind, "*.wav")))]
            for kind in ("positive", "negative", "noise")}


def trim_silence(clip: np.ndarray, frame: int = 320, keep: float = 0.1,
                 pad_sec: float = 0.3) -> np.ndarray:
    """The spoken part of a recording, with the same 0.3 s either side the
    TTS clips get. The feature is the last 1.28 s of a clip, so the words
    have to sit at its end, not somewhere in two seconds of room."""
    frames = len(clip) // frame
    if not frames:
        return clip
    rms = np.sqrt((clip[: frames * frame].reshape(frames, frame) ** 2).mean(axis=1))
    if rms.max() == 0:
        return clip
    loud = np.nonzero(rms >= keep * rms.max())[0]
    pad = np.zeros(int(TARGET_RATE * pad_sec), dtype=np.float32)
    return np.concatenate([pad, clip[loud[0] * frame:(loud[-1] + 1) * frame], pad])


def slice_noise(clip: np.ndarray, seconds: float = 2.0) -> list:
    size = int(TARGET_RATE * seconds)
    return [clip[i:i + size] for i in range(0, len(clip) - size + 1, size)]


def split_held_out(items: list, every: int = HELD_OUT_EVERY) -> tuple:
    test = items[every - 1::every]
    train = [x for i, x in enumerate(items) if (i + 1) % every]
    return train, test


# ─── Testing a model the way the app runs it ──────────────────────────────────

def stream_max(model_path: str, clip: np.ndarray) -> float:
    """The highest score a model gives a clip, fed 80 ms at a time, as the
    running wake listener does.

    A fresh extractor starts full of random noise, which the running app
    only has for a moment at startup. So two seconds of silence go first,
    and only scores from where the clip starts count — otherwise a model
    could look like it wakes on everything."""
    oww = Model(wakeword_models=[model_path], inference_framework="onnx")
    lead = np.zeros(int(TARGET_RATE * STREAM_LEAD_SEC), dtype=np.float32)
    tail = np.zeros(TARGET_RATE, dtype=np.float32)
    pcm = (np.clip(np.concatenate([lead, clip, tail]), -1, 1) * 32767).astype(np.int16)
    best = 0.0
    for i in range(0, len(pcm) - CHUNK + 1, CHUNK):
        score = max(oww.predict(pcm[i:i + CHUNK]).values())
        if i + CHUNK > len(lead):
            best = max(best, score)
    return float(best)


def evaluate(model_path: str, test_pos: list, test_neg: list, scorer=stream_max) -> tuple:
    return ([scorer(model_path, c) for c in test_pos],
            [scorer(model_path, c) for c in test_neg])


def report(label: str, pos: list, neg: list) -> list:
    def woke(scores):
        return ", ".join(f"{sum(s >= t for s in scores)}/{len(scores)} at {t:g}"
                         for t in THRESHOLDS)

    lines = []
    lines.append(f"{label:<5} Hey Fager:      woke {woke(pos)} · median "
                 f"{statistics.median(pos):.2f}" if pos else f"{label:<5} Hey Fager: none held back")
    lines.append(f"{label:<5} not Hey Fager:  woke {woke(neg)} · highest "
                 f"{max(neg):.2f}" if neg else f"{label:<5} not Hey Fager: none held back")
    return lines


def install(candidate=CANDIDATE_PATH, target=OUTPUT_PATH) -> str:
    """Put the candidate in place, backing the running model up first."""
    candidate, target = str(candidate), str(target)
    if not os.path.exists(candidate):
        raise FileNotFoundError(f"no candidate model at {candidate} — train one first")
    backup = ""
    if os.path.exists(target):
        stem = os.path.splitext(os.path.basename(target))[0]
        backup = os.path.join(os.path.dirname(target),
                              f"{stem}.backup-{time.strftime('%Y%m%d-%H%M%S')}.onnx")
        shutil.copy2(target, backup)
    shutil.copy2(candidate, target)
    return backup


# ─── Feature extraction ───────────────────────────────────────────────────────

def extract_features(clips: list[np.ndarray], pre, label: str,
                     every_position: bool = False) -> np.ndarray:
    """
    Features the way the running wake listener computes them: each clip fed
    80 ms at a time, after a second of silence, through the same streaming
    extractor, reading the last 16 frames (N, 1536 flattened).

    They used to come from embed_clips over the whole clip at once, which
    gives different numbers: a model trained that way scored a median 0.11 on
    its own training clips once they were streamed, as the app hears them.

    A positive gives the window as the phrase ends. A negative gives every
    window along it (every_position), because the app checks every 80 ms and
    no moment of a phrase that isn't "Hey Fager" may score high.
    """
    features = []
    quiet = np.zeros(TARGET_RATE, dtype=np.int16)

    for i, clip in enumerate(clips):
        pcm = (np.clip(clip, -1, 1) * 32767).astype(np.int16)
        try:
            pre.reset()
            for s in range(0, len(quiet), CHUNK):
                pre(quiet[s:s + CHUNK])
            for s in range(0, len(pcm) - CHUNK + 1, CHUNK):
                pre(pcm[s:s + CHUNK])
                if every_position:
                    features.append(pre.get_features(16).reshape(-1))
            if not every_position:
                features.append(pre.get_features(16).reshape(-1))
        except Exception as e:
            print(f"\n  [Feature error] {label} clip {i}: {e}")
            continue

        if (i + 1) % 20 == 0:
            print(f"  [{label}] {i+1}/{len(clips)} done")

    return np.array(features, dtype=np.float32)


# ─── ONNX model construction ──────────────────────────────────────────────────

def build_onnx_model(weights: np.ndarray, bias: float,
                     scaler_mean: np.ndarray, scaler_scale: np.ndarray) -> onnx.ModelProto:
    """
    Build an ONNX model that matches openwakeword's expected I/O:
      Input:  "x.1"  shape [1, 16, 96]  float32
      Output: "53"   shape [1, 1]        float32

    The graph:
      Reshape([1,16,96] → [1,1536]) → Standardize → MatMul → Add → Sigmoid
    """
    W = weights.astype(np.float32).reshape(1536, 1)
    b = np.array([bias], dtype=np.float32)
    mean = scaler_mean.astype(np.float32).reshape(1, 1536)
    scale = scaler_scale.astype(np.float32).reshape(1, 1536)

    shape_init = numpy_helper.from_array(np.array([1, 1536], dtype=np.int64), name="reshape_shape")
    mean_init  = numpy_helper.from_array(mean,  name="scaler_mean")
    scale_init = numpy_helper.from_array(scale, name="scaler_scale")
    W_init     = numpy_helper.from_array(W,     name="lr_weight")
    b_init     = numpy_helper.from_array(b,     name="lr_bias")

    # Nodes
    reshape = helper.make_node("Reshape",  inputs=["x.1", "reshape_shape"], outputs=["flat"])
    sub     = helper.make_node("Sub",      inputs=["flat", "scaler_mean"],   outputs=["centered"])
    div     = helper.make_node("Div",      inputs=["centered", "scaler_scale"], outputs=["scaled"])
    matmul  = helper.make_node("MatMul",   inputs=["scaled", "lr_weight"],   outputs=["logit"])
    add     = helper.make_node("Add",      inputs=["logit", "lr_bias"],      outputs=["logit_b"])
    sigmoid = helper.make_node("Sigmoid",  inputs=["logit_b"],               outputs=["53"])

    input_info  = helper.make_tensor_value_info("x.1", TensorProto.FLOAT, [1, 16, 96])
    output_info = helper.make_tensor_value_info("53",  TensorProto.FLOAT, [1, 1])

    graph = helper.make_graph(
        [reshape, sub, div, matmul, add, sigmoid],
        "hey_fager",
        [input_info],
        [output_info],
        initializer=[shape_init, mean_init, scale_init, W_init, b_init],
    )
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 13)])
    model.ir_version = 7
    onnx.checker.check_model(model)
    return model


def fit_candidate(parts: list, pre, out_path: str) -> None:
    """Train on (clips, label, weight, name) parts and save the model.
    Weights let Mo's recordings count for more than a robot voice's."""
    print("[Train] Extracting features...")
    X_parts, y, weights = [], [], []
    for clips, label, weight, name in parts:
        if not clips:
            continue
        feats = extract_features(clips, pre, name, every_position=(label == 0))
        X_parts.append(feats)
        y += [label] * len(feats)
        weights += [weight] * len(feats)
    X, y, weights = np.vstack(X_parts), np.array(y), np.array(weights)
    print(f"[Train] {int(y.sum())} positive and {int((1 - y).sum())} negative samples")

    # Standardise
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)

    # Train
    print("[Train] Training LogisticRegression...")
    clf = LogisticRegression(C=1.0, class_weight="balanced", max_iter=2000, solver="lbfgs")
    clf.fit(X_scaled, y, sample_weight=weights)

    preds = clf.predict(X_scaled)
    print("\n[Train] Training set report:")
    print(classification_report(y, preds, target_names=["negative", "hey_fager"],
                                zero_division=0))

    # Build and save ONNX
    print("[Train] Building ONNX model...")
    onnx_model = build_onnx_model(
        weights=clf.coef_[0],
        bias=float(clf.intercept_[0]),
        scaler_mean=scaler.mean_,
        scaler_scale=scaler.scale_,
    )
    onnx.save(onnx_model, out_path)
    print(f"[Train] Saved the candidate: {out_path}")


# ─── Main ─────────────────────────────────────────────────────────────────────

def main(argv=None, samples_dir=SAMPLES_DIR, candidate_path=CANDIDATE_PATH,
         model_path=OUTPUT_PATH, tts=None):
    argv = sys.argv[1:] if argv is None else argv
    if "--install" in argv:
        backup = install(candidate_path, model_path)
        print(f"[Train] Installed {candidate_path} as {model_path}.")
        if backup:
            print(f"[Train] The old model is kept as {backup}.")
        print("[Train] Restart El Fager to use it.")
        return

    os.makedirs(os.path.dirname(candidate_path), exist_ok=True)
    print("[Train] Loading openwakeword embedding pipeline...")
    pre = AudioFeatures(inference_framework="onnx")

    print("[Train] Generating audio samples (this takes 1-2 minutes)...")
    pos_clips, neg_clips = (tts or generate_samples)()

    if len(pos_clips) < 5:
        print("[Train] ERROR: too few positive clips — check pyttsx3 setup.")
        sys.exit(1)

    recorded = load_recorded(samples_dir)
    real_pos, test_pos = split_held_out([trim_silence(c) for c in recorded["positive"]])
    real_neg, test_neg = split_held_out([trim_silence(c) for c in recorded["negative"]])
    noise_train, noise_test = split_held_out(
        [w for c in recorded["noise"] for w in slice_noise(c)])
    real_neg += noise_train
    test_neg += noise_test
    if not recorded["positive"]:
        print(f"[Train] No recordings in {samples_dir} — training on TTS voices only. "
              "Record some with scripts/record_wake_samples.py.")
    print(f"[Train] Mo's recordings: {len(real_pos)} Hey Fager + {len(real_neg)} other "
          f"to train on; {len(test_pos)} + {len(test_neg)} held back to test.")

    fit_candidate([(pos_clips, 1, 1.0, "TTS POS"), (real_pos, 1, REAL_WEIGHT, "MO POS"),
                   (neg_clips, 0, 1.0, "TTS NEG"), (real_neg, 0, REAL_WEIGHT, "MO NEG")],
                  pre, candidate_path)

    if not (test_pos or test_neg):
        print("[Train] Nothing held back to test on.")
        return
    print("\n[Train] On the recordings held back (never trained on):")
    for label, path in (("old", model_path), ("new", candidate_path)):
        if os.path.exists(path):
            for line in report(label, *evaluate(path, test_pos, test_neg)):
                print("  " + line)
    print("\n[Train] To use the new model: "
          "python -X utf8 scripts/train_hey_fager.py --install")


if __name__ == "__main__":
    main()
