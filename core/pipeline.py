import queue
import re
import threading
import time

from PyQt6.QtCore import QThread, pyqtSignal

from core import ducking, turn_profile
from core.brain import Brain
from core.memory import Memory
from core.voice_in import VoiceInput
from core.voice_out import VoiceOutput

SCREENSHOT_TRIGGERS = (
    "what's on my screen", "what is on my screen",
    "look at my screen", "what do you see",
    "read this", "what's this", "what is this",
)

# Conversation mode: after El Fager replies, keep listening this long for a
# follow-up before ending the conversation. Context persists across turns.
FOLLOWUP_WINDOW_SEC = 6.0
# While something is staged the mic waits longer: you have to read what it
# wrote before you can say yes to it.
STAGED_WINDOW_SEC = 20.0
MAX_TURNS_PER_CONVERSATION = 10

# Phrases that end the conversation immediately (no follow-up window).
END_PHRASES = (
    "thanks", "thank you", "that's all", "bye", "goodbye", "stop",
)


def _is_screenshot_trigger(text: str) -> bool:
    text_lower = text.lower().strip()
    return any(t in text_lower for t in SCREENSHOT_TRIGGERS)


def _capture_for_turn() -> "tuple[str, str] | tuple[None, None]":
    """The picture for "what's this" — see screen_tool.capture_for_mo."""
    from tools import screen_tool
    return screen_tool.capture_for_mo()


def _is_end_phrase(text: str) -> bool:
    words = text.lower().strip().rstrip(".!").split()
    return len(words) <= 3 and any(p in text.lower() for p in END_PHRASES)


# Sentence boundary for streaming TTS: an ender followed by whitespace (so
# "3.5" never splits mid-number), or a newline.
_SENT_END_RE = re.compile(r"[.!?…]\s|\n")


def _pop_sentences(state: dict) -> "list[str]":
    """Remove and return all complete sentences from state['buf']."""
    sentences = []
    while True:
        m = _SENT_END_RE.search(state["buf"])
        if not m:
            return sentences
        cut = m.end()
        s = state["buf"][:cut].strip()
        state["buf"] = state["buf"][cut:]
        if s:
            sentences.append(s)


class PipelineWorker(QThread):
    """
    Runs the full voice interaction loop in a background thread.
    Signals drive all overlay state changes — never touch Qt widgets directly from here.

    Voice input runs in conversation mode: after each spoken reply, the mic
    reopens for FOLLOWUP_WINDOW_SEC and Brain context persists, so Mo can say
    "and tomorrow?" without repeating himself. The conversation (and context)
    ends on silence, an end phrase, or MAX_TURNS_PER_CONVERSATION.
    Text input stays single-turn (the text box has its own history UX).

    state_update(state, transcript, response):
      state ∈ {"listening", "processing", "speaking"}
      transcript: what Mo said (empty while listening)
      response: El Fager's reply (empty until speaking)
    """

    state_update = pyqtSignal(str, str, str)
    done = pyqtSignal()
    error = pyqtSignal(str)
    # The answer so far, re-sent each time a sentence goes to the voice, so
    # the transcript fills in as it is spoken instead of all at once.
    answer_text = pyqtSignal(str)
    # How loud Mo is, 0..1, for every slice of audio while the mic is open,
    # so the Cockpit's sphere can swell with the voice it is hearing.
    mic_level = pyqtSignal(float)

    def __init__(
        self,
        voice_in: VoiceInput,
        brain: Brain,
        voice_out: VoiceOutput,
        memory: Memory,
        text_input: "str | None" = None,
    ):
        super().__init__()
        self.voice_in = voice_in
        self.brain = brain
        self.voice_out = voice_out
        self.memory = memory
        self.text_input = text_input
        self._cancelled = threading.Event()

    # ── Cancellation ───────────────────────────────────────────────────────

    def cancel(self) -> None:
        """Wind the turn down at the next stage boundary.

        QThread.quit() does nothing here: it asks a thread's event loop to
        exit, and run() below is a plain blocking function with no event loop.
        The overlay called quit() and then wait(2000), which froze the UI for
        two seconds and left the worker running anyway — so closing and
        reopening produced a window that never appeared while the orphaned
        worker carried on holding the microphone.

        Safe from any thread: it sets a flag, stops the recorder, and cuts
        playback. Whatever stage is in flight finishes, then run() returns.
        """
        self._cancelled.set()
        try:
            self.voice_in.stop_recording()
        except Exception:
            pass
        try:
            self.voice_out.stop()
        except Exception:
            pass

    @property
    def cancelled(self) -> bool:
        return self._cancelled.is_set()

    def _should_stop(self, monitor=None):
        """Playback stops for a barge-in or for a cancel, whichever comes."""
        if monitor is None:
            return self._cancelled.is_set
        return lambda: self._cancelled.is_set() or monitor.tripped.is_set()

    def run(self):
        try:
            if self.text_input:
                transcript = self.text_input.strip()
                if transcript and not self._cancelled.is_set():
                    turn_profile.begin("typed turn")
                    self._one_turn(transcript)
                    self.brain.reset_conversation()
                return

            if not self.voice_in.is_ready():
                self.state_update.emit("processing", "Loading Whisper model...", "")
                loaded = self.voice_in.wait_until_ready(timeout=180)
                if not loaded:
                    self.error.emit("Whisper model failed to load. Check your internet connection and try again.")
                    return

            try:
                for turn in range(MAX_TURNS_PER_CONVERSATION):
                    if self._cancelled.is_set():
                        break
                    turn_profile.begin(f"voice turn {turn}")
                    self.state_update.emit("listening", "", "")
                    turn_profile.start("record")
                    # Other apps' audio drops while the mic is open, so it
                    # hears Mo and not the song El Fager just put on.
                    with ducking.ducked():
                        audio = self.voice_in.record_audio(
                            start_timeout_sec=self._listen_window(turn),
                            on_level=self.mic_level.emit,
                        )
                    turn_profile.end("record")
                    if self._cancelled.is_set():
                        break
                    if audio is None:
                        # A wake with nothing behind it is a false accept, and
                        # that is exactly the number worth knowing.
                        self._note_wake(heard=False)
                        if turn == 0:
                            return
                        break  # follow-up window closed — conversation over

                    self.state_update.emit("processing", "Transcribing...", "")
                    turn_profile.start("stt")
                    t_stt = time.monotonic()
                    transcript = self.voice_in.transcribe(audio)
                    turn_profile.end("stt")
                    print(f"[El Fager] timing: STT {time.monotonic() - t_stt:.2f}s")
                    if self._cancelled.is_set():
                        break          # do not spend a turn nobody is watching
                    self._note_wake(heard=bool(transcript))
                    if not transcript:
                        if turn == 0:
                            self.error.emit("Nothing heard — please try again")
                            return
                        break

                    self._one_turn(transcript)
                    if _is_end_phrase(transcript):
                        break
            finally:
                self.brain.reset_conversation()

        except Exception as e:
            self.error.emit(f"Pipeline error: {e}")
            print(f"[El Fager] Pipeline exception: {e}")

        finally:
            self.done.emit()

    def _listen_window(self, turn: int) -> "float | None":
        """How long to wait for speech before giving up.

        The first turn waits as long as it takes — you summoned it. Later
        turns get the short follow-up window, EXCEPT while an action is armed:
        confirming by voice is the design's own path ( / "send"), and it
        cannot be the path if the mic has already closed by the time you look
        at what it staged.
        """
        if turn == 0:
            return None
        try:
            from core import staging
            if staging.current() is not None:
                return STAGED_WINDOW_SEC
        except Exception:
            pass
        return FOLLOWUP_WINDOW_SEC

    @staticmethod
    def _note_wake(heard: bool) -> None:
        try:
            from core import wake_metrics
            wake_metrics.note_speech(heard)
        except Exception:
            pass          # measurement never breaks a turn

    def _one_turn(self, transcript: str) -> None:
        """Process one utterance: think, remember, speak.

        The Claude response streams in; complete sentences are handed to
        voice_out.speak_stream as they arrive, so speech starts on the first
        sentence instead of after the full reply is buffered."""
        from core import progress

        t_start = time.monotonic()
        progress.begin_turn(transcript)   # last turn's steps leave the stage
        self.state_update.emit("processing", transcript, "")
        # Chroma query plus a local embed, and it sits squarely on the turn
        # path — it used to be hidden inside the time-to-first-token figure.
        turn_profile.start("memory")
        memory_context = self.memory.get_recent_context(transcript)
        turn_profile.end("memory")

        sent_q: "queue.Queue[str | None]" = queue.Queue()
        state = {"buf": "", "said": ""}
        streamed = threading.Event()
        spoke_state = {"emitted": False}
        first_token = [0.0]

        def on_text(delta: str) -> None:
            if not first_token[0]:
                first_token[0] = time.monotonic()
                turn_profile.mark("first_token")
                print(f"[El Fager] timing: first Claude token "
                      f"+{first_token[0] - t_start:.2f}s")
            state["buf"] += delta
            for s in _pop_sentences(state):
                streamed.set()
                if not spoke_state["emitted"]:
                    spoke_state["emitted"] = True
                    self.state_update.emit("speaking", transcript, "")
                sent_q.put(s)
                state["said"] = f"{state['said']} {s}".strip()
                self.answer_text.emit(state["said"])

        # Barge-in: listen while it talks, and cut the moment Mo talks over it.
        from core import barge_in as _barge_in

        monitor = _barge_in.BargeInMonitor() if _barge_in.enabled() else None
        if monitor is not None:
            monitor.start()

        speaker = threading.Thread(
            target=self.voice_out.speak_stream,
            args=(iter(sent_q.get, None),),
            kwargs={"should_stop": self._should_stop(monitor)},
            daemon=True,
        )
        speaker.start()

        try:
            if _is_screenshot_trigger(transcript):
                from tools.screen_tool import delete_temp_screenshot
                b64, tmp_path = _capture_for_turn()
                if b64 is None:
                    self.error.emit("Screenshot failed — couldn't capture screen")
                    return
                response = self.brain.chat_with_screenshot(
                    transcript, b64, memory_context, on_text=on_text
                )
                delete_temp_screenshot(tmp_path)
            else:
                response = self.brain.chat(transcript, memory_context, on_text=on_text)
        finally:
            tail = state["buf"].strip()
            if tail:
                streamed.set()
                sent_q.put(tail)
                state["said"] = f"{state['said']} {tail}".strip()
                self.answer_text.emit(state["said"])
            sent_q.put(None)  # end-of-stream sentinel — speaker exits after draining

        self.memory.store_conversation_summary(transcript, response)
        self.state_update.emit("speaking", transcript, response)
        speaker.join()
        interrupted = monitor is not None and monitor.tripped.is_set()
        if not streamed.is_set() and not interrupted:
            # Nothing streamed (API error text, offline fallback) — speak it whole.
            self.voice_out.speak(
                response,
                should_stop=self._should_stop(monitor),
            )
            interrupted = monitor is not None and monitor.tripped.is_set()
        if monitor is not None:
            monitor.stop()
        turn_profile.mark("last_audio")
        turn_profile.finish()
        if interrupted:
            # The half-spoken answer collapses to a dim caption and the mic
            # opens again — being talked over means Mo has the floor now.
            self.state_update.emit("interrupted", transcript, response)
