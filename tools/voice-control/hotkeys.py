"""Global keyboard shortcuts (they work while another window, e.g. HereSphere, has the focus).
Optional dependency: pynput. Keys are only observed, never swallowed.

Numpad keys need NumLock ON. A held key fires once (no auto-repeat)."""
import threading

NUMPAD_VK = {0x60: "num0", 0x61: "num1", 0x62: "num2", 0x63: "num3", 0x64: "num4", 0x65: "num5", 0x66: "num6",
             0x67: "num7", 0x68: "num8", 0x69: "num9", 0x6A: "num_multiply", 0x6B: "num_add", 0x6D: "num_subtract",
             0x6E: "num_decimal", 0x6F: "num_divide"}

# how a key is written on the keyboard (for the cheat sheet)
CAPTIONS = {"num_multiply": "Num *", "num_add": "Num +", "num_subtract": "Num -", "num_decimal": "Num .", "num_divide": "Num /"}


def caption(key: str) -> str:
    if key in CAPTIONS:
        return CAPTIONS[key]
    if key.startswith("num") and key[3:].isdigit():
        return f"Num {key[3:]}"
    return key.capitalize() if len(key) > 3 else key.upper()


def key_name(k):
    vk = getattr(k, "vk", None)
    if vk in NUMPAD_VK:
        return NUMPAD_VK[vk]
    name = getattr(k, "name", None)
    if name:
        return name.lower()
    char = getattr(k, "char", None)
    return char.lower() if char else None


def cheat_sheet(keys: dict, intents) -> str:
    """text lines 'Num 1  -> mode 1 ...' for the start-up banner and the README"""
    label = {i.id: i.phrases[0] for i in intents}
    lines = []
    for key, target in keys.items():
        what = "listening on/off" if target == "@mute" else label.get(target, target)
        lines.append(f"  {caption(key):<8} {what}")
    return "\n".join(lines)


class Hotkeys:
    def __init__(self, keys: dict, on_intent, on_mute=None):
        from pynput import keyboard  # imported late: not needed for text / wav
        self.keys, self.on_intent, self.on_mute = {k.lower(): v for k, v in keys.items()}, on_intent, on_mute
        self._down = set()
        self._lock = threading.Lock()

        def on_press(k):
            name = key_name(k)
            if name not in self.keys:
                return
            with self._lock:
                if name in self._down:
                    return
                self._down.add(name)
            self._fire(self.keys[name])

        def on_release(k):
            name = key_name(k)
            with self._lock:
                self._down.discard(name)

        self._listener = keyboard.Listener(on_press=on_press, on_release=on_release)
        self._listener.start()

    def _fire(self, target):
        # a thread of its own: an action with several steps must not block the keyboard hook
        if target == "@mute":
            if self.on_mute:
                threading.Thread(target=self.on_mute, daemon=True).start()
        else:
            threading.Thread(target=self.on_intent, args=(target,), daemon=True).start()

    def stop(self):
        self._listener.stop()
