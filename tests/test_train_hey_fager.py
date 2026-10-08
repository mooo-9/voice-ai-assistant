"""Retraining the wake word on Mo's own recordings.

The model was trained only on text-to-speech voices and scores Mo's own
"Hey Fager" around the threshold or near zero. The training script now adds
his recordings, tests on ones it held back, and replaces the running model
only when asked, keeping a backup."""
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import record_wake_samples as rec  # noqa: E402
import train_hey_fager as train  # noqa: E402

RATE = train.TARGET_RATE


def _tone(seconds, amp=0.5):
    t = np.arange(int(seconds * RATE)) / RATE
    return (amp * np.sin(2 * np.pi * 220 * t)).astype(np.float32)


def _silence(seconds):
    return np.zeros(int(seconds * RATE), dtype=np.float32)


class TestTrim:
    def test_the_words_are_kept_with_a_little_room_around_them(self):
        clip = np.concatenate([_silence(1.0), _tone(0.5), _silence(1.0)])
        trimmed = train.trim_silence(clip)
        assert abs(len(trimmed) / RATE - (0.5 + 2 * 0.3)) < 0.05
        assert np.abs(trimmed[: int(0.25 * RATE)]).max() == 0      # the padding is silent

    def test_a_silent_clip_is_left_alone(self):
        clip = _silence(2.0)
        assert len(train.trim_silence(clip)) == len(clip)


def test_a_minute_of_room_becomes_two_second_pieces():
    pieces = train.slice_noise(_silence(5.0))
    assert [len(p) for p in pieces] == [2 * RATE, 2 * RATE]


def test_every_fourth_recording_is_held_back():
    train_part, test_part = train.split_held_out(list(range(8)))
    assert test_part == [3, 7]
    assert train_part == [0, 1, 2, 4, 5, 6]


class TestLoadRecorded:
    def test_it_reads_what_the_recorder_wrote(self, tmp_path):
        rec._save(tmp_path / "positive" / "a.wav", _tone(2.0))
        rec._save(tmp_path / "positive" / "b.wav", _tone(2.0))
        rec._save(tmp_path / "noise" / "room.wav", _silence(3.0))
        got = train.load_recorded(tmp_path)
        assert [len(got[k]) for k in ("positive", "negative", "noise")] == [2, 0, 1]
        assert got["positive"][0].dtype == np.float32
        assert np.abs(got["positive"][0]).max() == pytest.approx(0.5, abs=0.01)

    def test_other_sample_rates_are_resampled(self, tmp_path):
        import scipy.io.wavfile as wavfile
        (tmp_path / "positive").mkdir()
        wavfile.write(tmp_path / "positive" / "hi.wav", 48000,
                      (np.zeros(48000) + 1000).astype(np.int16))
        clip = train.load_recorded(tmp_path)["positive"][0]
        assert len(clip) == RATE

    def test_no_folder_means_no_recordings(self, tmp_path):
        got = train.load_recorded(tmp_path / "missing")
        assert got == {"positive": [], "negative": [], "noise": []}


def test_the_report_counts_wakes_at_each_threshold():
    lines = train.report("new", [0.9, 0.4, 0.2], [0.1, 0.36])
    assert "woke 2/3 at 0.35, 1/3 at 0.5" in lines[0]
    assert "woke 1/2 at 0.35, 0/2 at 0.5" in lines[1]
    assert "highest 0.36" in lines[1]


def test_evaluation_scores_every_held_back_clip():
    scored = []

    def scorer(model, clip):
        scored.append(model)
        return float(clip.max())

    pos, neg = train.evaluate("m.onnx", [_tone(0.5, 0.8)], [_tone(0.5, 0.1)] * 2, scorer)
    assert pos == [pytest.approx(0.8, abs=0.01)]
    assert len(neg) == 2
    assert scored == ["m.onnx"] * 3


class TestTrainedTheWayItListens:
    """The features were computed over each whole clip at once, and the app
    computes them 80 ms at a time. They differ: the running model scored 1.0
    on its own training clip the first way and 0.1 on the same audio streamed.
    A model must score its own training clips high when they are streamed."""

    def test_a_trained_model_hears_its_own_clips_when_streamed(self, tmp_path):
        # Synthetic tones against noise separate either way; the mismatch
        # showed on speech, so this uses the Windows voices the script uses.
        from openwakeword.utils import AudioFeatures
        voices = train._voices()
        if not voices:
            pytest.skip("no Windows text-to-speech voices")

        def say(text, rate):
            clip = train._tts_to_wav(text, voices[0], rate)
            return train.trim_silence(clip) if clip is not None else None

        pos = [c for c in (say("Hey Fager", r) for r in (130, 150, 170)) if c is not None]
        neg = [c for c in (say(t, 150) for t in ("What time is it", "Play some music",
                                                  "Hello there")) if c is not None]
        # A CI runner lists voices but renders nothing through them: every
        # clip comes back None, and fitting on no audio says only "need at
        # least one array to concatenate". This test needs real speech.
        if len(pos) < 2 or not neg:
            pytest.skip("Windows text-to-speech rendered no audio")
        out = tmp_path / "candidate.onnx"
        train.fit_candidate([(pos * 2, 1, 1.0, "POS"), (neg * 2, 0, 1.0, "NEG")],
                            AudioFeatures(inference_framework="onnx"), str(out))
        assert train.stream_max(str(out), pos[1]) > 0.5
        assert train.stream_max(str(out), neg[0]) < 0.5


class TestInstall:
    def test_the_running_model_is_backed_up_then_replaced(self, tmp_path):
        target, candidate = tmp_path / "hey_fager.onnx", tmp_path / "cand.onnx"
        target.write_bytes(b"old")
        candidate.write_bytes(b"new")
        backup = Path(train.install(candidate, target))
        assert target.read_bytes() == b"new"
        assert backup.read_bytes() == b"old"
        assert backup.name.startswith("hey_fager.backup-")

    def test_without_a_candidate_nothing_changes(self, tmp_path):
        target = tmp_path / "hey_fager.onnx"
        target.write_bytes(b"old")
        with pytest.raises(FileNotFoundError):
            train.install(tmp_path / "missing.onnx", target)
        assert target.read_bytes() == b"old"
        assert list(tmp_path.iterdir()) == [target]
