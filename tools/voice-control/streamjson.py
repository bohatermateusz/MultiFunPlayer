"""An incremental reader for a flat JSON object arriving as a token stream (structured outputs).

Feed the text as it comes; it reports every top-level field the moment its value is complete, and, for the field being
written at the moment, the text deltas of its string value - so a line can be spoken while the model is still writing it.
Nested objects / arrays are reported whole (parsed) when closed. Good enough for the persona's answer; not a general parser."""
import json


class StreamObject:
    def __init__(self):
        self.fields = {}          # completed fields
        self.order = []           # keys in the order they were closed
        self.current_key = None   # the key whose string value is being written
        self._buf = ""            # unconsumed text
        self._state = "start"     # start | key | colon | value | string | nested | after
        self._key = ""
        self._str = ""            # the decoded string so far (current string value)
        self._raw = ""            # raw text of a nested value
        self._depth = 0
        self._in_str = False      # inside a string within a nested value
        self._esc = False
        self.done = False

    def feed(self, text):
        """-> list of events: ("field", key, value) when a field is complete, ("delta", key, text) for string deltas"""
        events = []
        self._buf += text
        while self._buf and not self.done:
            c = self._buf[0]
            if self._state == "start":
                self._buf = self._buf[1:]
                if c == "{":
                    self._state = "key"
                continue
            if self._state == "key":
                if c == '"':
                    self._key, self._state = "", "keystr"
                elif c == "}":
                    self.done = True
                self._buf = self._buf[1:]
                continue
            if self._state == "keystr":
                end = self._buf.find('"')
                if end < 0:
                    self._key += self._buf
                    self._buf = ""
                    break
                self._key += self._buf[:end]
                self._buf = self._buf[end + 1:]
                self._state = "colon"
                continue
            if self._state == "colon":
                self._buf = self._buf[1:]
                if c == ":":
                    self._state = "value"
                continue
            if self._state == "value":
                if c in " \t\r\n":
                    self._buf = self._buf[1:]
                    continue
                if c == '"':
                    self._buf = self._buf[1:]
                    self._str, self._esc = "", False
                    self.current_key = self._key
                    self._state = "string"
                    continue
                if c in "[{":
                    self._raw, self._depth, self._in_str, self._esc = "", 0, False, False
                    self._state = "nested"
                    continue
                # a scalar: number, true, false, null - ends at , or }
                m = 0
                while m < len(self._buf) and self._buf[m] not in ",}":
                    m += 1
                if m >= len(self._buf):
                    break                           # wait for more
                raw = self._buf[:m].strip()
                self._buf = self._buf[m:]
                try:
                    value = json.loads(raw)
                except ValueError:
                    value = raw
                events.append(self._close(value))
                self._state = "after"
                continue
            if self._state == "string":
                out = ""
                i = 0
                closed = False
                while i < len(self._buf):
                    ch = self._buf[i]
                    if self._esc:
                        if ch == "u":
                            if i + 4 >= len(self._buf):
                                break           # wait for the 4 hex digits
                            try:
                                out += chr(int(self._buf[i + 1:i + 5], 16))
                            except ValueError:
                                pass
                            i += 5
                        else:
                            out += {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f"}.get(ch, ch)
                            i += 1
                        self._esc = False
                        continue
                    if ch == "\\":
                        self._esc = True
                        i += 1
                        continue
                    if ch == '"':
                        closed = True
                        i += 1
                        break
                    out += ch
                    i += 1
                self._buf = self._buf[i:]
                if out:
                    self._str += out
                    events.append(("delta", self._key, out))
                if closed:
                    events.append(self._close(self._str))
                    self.current_key = None
                    self._state = "after"
                    continue
                break
            if self._state == "nested":
                i = 0
                finished = False
                while i < len(self._buf):
                    ch = self._buf[i]
                    self._raw += ch
                    i += 1
                    if self._in_str:
                        if self._esc:
                            self._esc = False
                        elif ch == "\\":
                            self._esc = True
                        elif ch == '"':
                            self._in_str = False
                        continue
                    if ch == '"':
                        self._in_str = True
                    elif ch in "[{":
                        self._depth += 1
                    elif ch in "]}":
                        self._depth -= 1
                        if self._depth == 0:
                            finished = True
                            break
                self._buf = self._buf[i:]
                if finished:
                    try:
                        value = json.loads(self._raw)
                    except ValueError:
                        value = self._raw
                    events.append(self._close(value))
                    self._state = "after"
                    continue
                break
            if self._state == "after":
                self._buf = self._buf[1:]
                if c == ",":
                    self._state = "key"
                elif c == "}":
                    self.done = True
                continue
        return events

    def _close(self, value):
        self.fields[self._key] = value
        self.order.append(self._key)
        return ("field", self._key, value)

    def has(self, *keys):
        return all(k in self.fields for k in keys)
