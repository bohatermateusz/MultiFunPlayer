"""Push-to-talk recording. Optional dependencies: sounddevice + numpy (microphone), pynput (keyboard).
A gamepad button works through XInput (Windows, ctypes, no extra package)."""
import sys
import threading
import time

XINPUT_BUTTONS = {"dpad_up": 0x1, "dpad_down": 0x2, "dpad_left": 0x4, "dpad_right": 0x8, "start": 0x10, "back": 0x20,
                  "left_thumb": 0x40, "right_thumb": 0x80, "lb": 0x100, "rb": 0x200, "a": 0x1000, "b": 0x2000,
                  "x": 0x4000, "y": 0x8000}


class PushToTalk:
    """is_pressed() is True while the key or the gamepad button is held."""

    def __init__(self, keyboard_key=None, gamepad_button=None):
        self._pressed_keys = False
        self._listener = None
        self._xinput = None
        self._button_mask = XINPUT_BUTTONS.get((gamepad_button or "").lower(), 0)
        if keyboard_key:
            from pynput import keyboard  # imported late: not needed for --text / --wav
            wanted = keyboard_key.lower()

            def name(k):
                return (getattr(k, "char", None) or getattr(k, "name", "") or "").lower()

            def on_press(k):
                if name(k) == wanted:
                    self._pressed_keys = True

            def on_release(k):
                if name(k) == wanted:
                    self._pressed_keys = False

            self._listener = keyboard.Listener(on_press=on_press, on_release=on_release)
            self._listener.start()
        if self._button_mask and sys.platform == "win32":
            import ctypes

            class State(ctypes.Structure):
                _fields_ = [("packet", ctypes.c_uint32), ("buttons", ctypes.c_uint16), ("lt", ctypes.c_ubyte),
                            ("rt", ctypes.c_ubyte), ("lx", ctypes.c_short), ("ly", ctypes.c_short),
                            ("rx", ctypes.c_short), ("ry", ctypes.c_short)]

            self._State = State
            self._xinput = ctypes.windll.xinput1_4

    def _gamepad_pressed(self):
        if not self._xinput:
            return False
        for index in range(4):
            state = self._State()
            if self._xinput.XInputGetState(index, __import__("ctypes").byref(state)) == 0 and state.buttons & self._button_mask:
                return True
        return False

    def is_pressed(self):
        return self._pressed_keys or self._gamepad_pressed()


def record_while_pressed(ptt, sample_rate=16000, min_seconds=0.4, max_seconds=8.0, device=None, poll=0.02):
    """Waits for the PTT, records while it is held, returns 16-bit mono PCM (or b'' if too short)."""
    import numpy as np
    import sounddevice as sd

    while not ptt.is_pressed():
        time.sleep(poll)

    chunks, lock = [], threading.Lock()

    def callback(indata, frames, t, status):
        with lock:
            chunks.append(indata.copy())

    start = time.monotonic()
    with sd.InputStream(samplerate=sample_rate, channels=1, dtype="int16", device=device, callback=callback):
        while ptt.is_pressed() and time.monotonic() - start < max_seconds:
            time.sleep(poll)
    duration = time.monotonic() - start
    if duration < min_seconds:
        return b""
    with lock:
        return np.concatenate(chunks).tobytes() if chunks else b""


def beep(kind: str):
    """short tones: ok / ignored / error (Windows only; silent elsewhere)"""
    try:
        import winsound
        tones = {"ok": [(880, 90)], "ignored": [(300, 120)], "error": [(300, 120), (220, 200)], "confirm": [(660, 90), (660, 90)]}
        for freq, ms in tones.get(kind, []):
            winsound.Beep(freq, ms)
    except Exception:
        pass


class SegmentDetector:
    """Cuts a stream of 16-bit mono blocks into utterances: speech starts when the level is clearly
    above the background noise (or a fixed threshold) for two blocks in a row and ends after
    end_silence of quiet. feed(block) -> PCM bytes of a finished utterance, or None.

    The noise floor is learned while nobody speaks; the first calibration_seconds are used only for
    that. Utterances shorter than min_seconds (coughs, clicks) are dropped, longer ones are cut at
    max_seconds."""

    def __init__(self, sample_rate=16000, threshold_db=None, margin_db=12.0, end_silence=0.7, pre_roll=0.3,
                 min_seconds=0.5, max_seconds=6.0, calibration_seconds=1.5, floor_min_db=-60.0):
        self.sample_rate = sample_rate
        self.fixed_threshold_db = threshold_db
        self.margin_db = margin_db
        self.end_silence = end_silence
        self.pre_roll = pre_roll
        self.min_seconds = min_seconds
        self.max_seconds = max_seconds
        self.calibration_seconds = calibration_seconds
        self.floor_min_db = floor_min_db
        self.noise_floor_db = None
        self.level_db = floor_min_db
        self._time = 0.0
        self._pre, self._blocks = [], []
        self._loud_run = 0
        self._speaking = False
        self._silence = 0.0
        self._duration = 0.0

    @staticmethod
    def level_of(block) -> float:
        import numpy as np
        samples = np.asarray(block, dtype="float64").reshape(-1)
        if samples.size == 0:
            return -120.0
        rms = float(np.sqrt(np.mean(samples * samples))) / 32768.0
        return 20.0 * np.log10(max(rms, 1e-7))

    @property
    def threshold_db(self):
        if self.fixed_threshold_db is not None:
            return self.fixed_threshold_db
        floor = self.noise_floor_db if self.noise_floor_db is not None else self.floor_min_db
        return max(floor, self.floor_min_db) + self.margin_db

    def reset(self):
        self._pre, self._blocks = [], []
        self._loud_run = 0
        self._speaking = False
        self._silence = 0.0
        self._duration = 0.0

    def feed(self, block):
        import numpy as np
        block = np.asarray(block, dtype="int16").reshape(-1)
        seconds = block.size / self.sample_rate
        self._time += seconds
        level = self.level_of(block)
        self.level_db = level

        if self._time <= self.calibration_seconds:
            self.noise_floor_db = level if self.noise_floor_db is None else max(self.noise_floor_db, level)
            return None

        loud = level > self.threshold_db
        if not self._speaking:
            if not loud:
                # slowly follow the background (a fan, the film in the room)
                self.noise_floor_db = level if self.noise_floor_db is None else 0.95 * self.noise_floor_db + 0.05 * level
            self._pre.append(block)
            while sum(b.size for b in self._pre) / self.sample_rate > self.pre_roll + 2 * seconds:
                self._pre.pop(0)
            self._loud_run = self._loud_run + 1 if loud else 0
            if self._loud_run >= 2:
                self._speaking = True
                self._blocks = list(self._pre)
                self._duration = sum(b.size for b in self._blocks) / self.sample_rate
                self._silence = 0.0
            return None

        self._blocks.append(block)
        self._duration += seconds
        self._silence = 0.0 if loud else self._silence + seconds
        if self._silence >= self.end_silence or self._duration >= self.max_seconds:
            speech = self._duration - self._silence
            pcm = np.concatenate(self._blocks).tobytes()
            self.reset()
            return pcm if speech >= self.min_seconds else None
        return None


def listen_segments(sample_rate=16000, device=None, stop=None, muted=None, block_seconds=0.02, **detector_options):
    """Generator: opens the microphone and yields the PCM of every utterance. `stop()` ends it,
    `muted()` pauses the detection (the microphone stays open)."""
    import queue

    import sounddevice as sd

    blocks = queue.Queue()
    detector = SegmentDetector(sample_rate=sample_rate, **detector_options)

    def callback(indata, frames, t, status):
        blocks.put(indata.copy())

    with sd.InputStream(samplerate=sample_rate, channels=1, dtype="int16", device=device,
                        blocksize=int(sample_rate * block_seconds), callback=callback):
        while not (stop and stop()):
            try:
                block = blocks.get(timeout=0.2)
            except queue.Empty:
                continue
            if muted and muted():
                detector.reset()
                continue
            pcm = detector.feed(block)
            if pcm:
                yield pcm


def meter(sample_rate=16000, device=None, seconds=15.0, **detector_options):
    """Prints the microphone level and the detection threshold (for tuning listen.threshold_db)."""
    import queue

    import sounddevice as sd

    blocks = queue.Queue()
    detector = SegmentDetector(sample_rate=sample_rate, **detector_options)
    end, last = time.monotonic() + seconds, 0.0
    with sd.InputStream(samplerate=sample_rate, channels=1, dtype="int16", device=device,
                        blocksize=int(sample_rate * 0.02), callback=lambda indata, f, t, s: blocks.put(indata.copy())):
        while time.monotonic() < end:
            try:
                block = blocks.get(timeout=0.2)
            except queue.Empty:
                continue
            pcm = detector.feed(block)
            now = time.monotonic()
            if now - last > 0.25:
                last = now
                bar = "#" * max(0, int((detector.level_db + 60) / 2))
                print(f"\r{detector.level_db:6.1f} dB (threshold {detector.threshold_db:6.1f}) {bar:<30}", end="", flush=True)
            if pcm:
                print(f"\n  speech detected: {len(pcm) / 2 / sample_rate:.1f}s")
    print()
