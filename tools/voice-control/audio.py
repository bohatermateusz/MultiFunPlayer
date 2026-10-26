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
