"""How the persona's lines are spoken: the provider order (ElevenLabs first, OpenAI as the fallback), streaming
playback, interruption by a new line or by the user's voice (barge-in), and the speech gate against echo.

A provider is a callable open(line, stage) -> iterator of PCM chunks; it raises TtsError when it cannot or will not
speak (no key, HTTP error, refused text, empty answer). If a provider fails BEFORE the first sound, the next one is
tried; if it fails in the middle of a line, the line is not repeated (that would play it twice)."""
import inspect
import threading

from tts import TtsError, pcm_chunks


def _short(msg):
    """the useful part of an API error: its 'message' when there is one, else the first 160 characters"""
    import re
    m = re.search(r"message['\"]?\s*:\s*['\"]([^'\"]+)", msg)
    return m.group(1) if m else msg[:160]


def _with_meta(provider):
    """providers written for (line, stage) keep working; new ones get (line, stage, meta)"""
    try:
        params = inspect.signature(provider).parameters
        takes_meta = len(params) >= 3 or any(p.kind == p.VAR_POSITIONAL for p in params.values())
    except (TypeError, ValueError):
        takes_meta = False
    if takes_meta:
        return provider
    return lambda line, stage, meta=None: provider(line, stage)


class Voice:
    def __init__(self, providers, order, play, gate=None, muted=lambda: False, log=print):
        self.providers = providers          # name -> open(line, stage[, meta])
        self.order = [n for n in order if n in providers]
        self.play = play                    # play(stream, stop) -> plays until the end or until stop() is true
        self.gate = gate
        self.muted = muted
        self.log = log
        self._stop = False
        self._thread = None
        self.last_provider = None

    def say(self, line, stage=0, meta=None, pcm=None, stream=None):
        """speak the line (pcm given: play that audio instead of asking a provider - the narration cache;
        stream given: a ready generator of PCM chunks, e.g. the websocket voice of the live mode)"""
        if (not self.providers and pcm is None and stream is None) or self.muted():
            return
        self._stop = True                    # a new line interrupts the previous one
        previous = self._thread
        if previous is not None:
            previous.join(2.0)
        self._stop = False
        if stream is not None:
            target, args = (self._run_stream, (stream, line, stage, meta))
        else:
            target, args = (self._run_pcm, (pcm,)) if pcm is not None else (self._run, (line, stage, meta))
        self._thread = threading.Thread(target=target, args=args, daemon=True)
        self._thread.start()

    def _run_stream(self, stream, line, stage, meta):
        """a ready chunk stream; when it fails before any sound, the ordinary providers speak the (whole) line"""
        if self.gate:
            self.gate.begin()
        counter = {"chunks": 0}
        try:
            self.last_provider = "stream"
            self.play(self._counted(stream, counter), lambda: self._stop)
            return
        except TtsError as e:
            self.log(f"  streamed voice failed: {e}")
        except Exception as e:
            self.log(f"  playback failed: {e}")
            return
        finally:
            if self.gate:
                self.gate.end()
        if not counter["chunks"] and line and not self._stop:
            self._run(line, stage, meta)

    def _run_pcm(self, pcm):
        if self.gate:
            self.gate.begin()
        try:
            self.last_provider = "cache"
            self.play(pcm_chunks(pcm), lambda: self._stop)
        except Exception as e:
            self.log(f"  playback failed: {e}")
        finally:
            if self.gate:
                self.gate.end()

    def speaking(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def interrupt(self):
        """the user starts to speak while she talks (headphones): she is cut off at once"""
        if self.speaking():
            self._stop = True

    def wait(self, timeout=5.0):
        if self._thread is not None:
            self._thread.join(timeout)

    def _run(self, line, stage, meta=None):
        if self.gate:
            self.gate.begin()
        try:
            for name in self.order:
                counter = {"chunks": 0}
                try:
                    stream = _with_meta(self.providers[name])(line, stage, meta)
                    self.last_provider = name
                    self.play(self._counted(stream, counter), lambda: self._stop)
                    return
                except TtsError as e:
                    msg = str(e)
                    if any(k in msg for k in ("HTTP 401", "missing_permissions", "quota_exceeded", "invalid_api_key")):
                        self.order = [n for n in self.order if n != name]
                        self.log(f"  text-to-speech ({name}) refused for good: {_short(msg)} - switched off for this session"
                                 + (f", using {self.order[0]}" if self.order else ""))
                    else:
                        self.log(f"  text-to-speech ({name}) failed: {_short(msg)}")
                    if counter["chunks"]:
                        return               # it had started: do not say the line twice
                except Exception as e:
                    self.log(f"  playback failed: {e}")
                    return
            self.log("  (no voice could speak this line)")
        finally:
            if self.gate:
                self.gate.end()

    @staticmethod
    def _counted(stream, counter):
        try:
            for chunk in stream:
                counter["chunks"] += 1
                yield chunk
        finally:
            close = getattr(stream, "close", None)
            if close:
                close()
