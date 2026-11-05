"""The conversation modes and the switch between them (F11).

classic - what the tool always did: an utterance is recorded whole, transcribed, answered, spoken.
live    - speech recognition as you speak (OpenAI Realtime), the safe word and plain commands run from the partial
          transcript, a request to her starts her thinking early, her answer is streamed sentence by sentence into a
          websocket voice. Target: her first word ~1-1.5 s after your last.
agents  - ElevenLabs Agents (see agents.py): one audio stream both ways, our persona as the agent's own LLM.
Every loop has start() / stop(); the manager swaps them without restarting the tool."""
import queue
import threading
import time

from tts import TtsError


class SentenceStream:
    """sentences pushed by the answer stream, read by the voice; close() ends it"""

    def __init__(self):
        self.q = queue.Queue()
        self.closed = False

    def push(self, text):
        if text:
            self.q.put(text)

    def close(self):
        self.closed = True
        self.q.put(None)

    def __iter__(self):
        while True:
            item = self.q.get()
            if item is None:
                return
            yield item

    def text(self):
        return ""


class ClassicLoop:
    """the old loop (audio.listen_segments -> transcribe -> handle), run in a thread so that it can be stopped"""

    def __init__(self, listen, process, log=print):
        self.listen, self.process, self.log = listen, process, log
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        try:
            for pcm in self.listen(lambda: self._stop.is_set()):
                if self._stop.is_set():
                    break
                self.process(pcm)
        except Exception as e:
            self.log(f"  classic loop stopped: {e}")

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(3.0)


class LiveLoop:
    """Realtime transcription + streamed answers. mic(rate, on_block, stop) opens the microphone and calls on_block(pcm)
    for every block until stop() is true (sounddevice in the tool, a fake in the tests)."""

    def __init__(self, controller, stt, mic, voice, ws_speaker=None, fallback_transcribe=None, detector=None,
                 headphones=True, muted=lambda: False, speculative=True, interrupt_on_speech=True, log=print, rate=24000):
        self.c, self.stt, self.mic, self.voice, self.ws = controller, stt, mic, voice, ws_speaker
        self.fallback_transcribe, self.detector = fallback_transcribe, detector
        self.headphones, self.muted, self.speculative, self.interrupt_on_speech = headphones, muted, speculative, interrupt_on_speech
        self.log, self.rate = log, rate
        self._stop = threading.Event()
        self._spec = {}                   # turn id -> {"text", "answer"|"error", "thread"}
        self._lock = threading.Lock()
        self.last_outcome = None
        stt.on_partial, stt.on_final = self.on_partial, self.on_final
        stt.on_speech_start = self.on_speech_start

    # ---- microphone ------------------------------------------------------------------------------------
    def start(self):
        self._stop.clear()
        self.stt.start()
        threading.Thread(target=lambda: self.mic(self.rate, self.on_block, lambda: self._stop.is_set()), daemon=True).start()

    def stop(self):
        self._stop.set()
        self.stt.stop()

    def on_block(self, pcm):
        if self.muted():
            return
        sent = self.stt.send_audio(pcm)
        if not sent and self.detector is not None and self.fallback_transcribe is not None:
            # the realtime session is down: the local detector cuts utterances and the batch model transcribes them
            import numpy as np
            was = self.detector.in_speech
            segment = self.detector.feed(np.frombuffer(pcm, dtype=np.int16))
            if self.detector.in_speech and not was:
                self.on_speech_start()
            if segment:
                try:
                    text = self.fallback_transcribe(segment)
                except Exception as e:
                    self.log(f"  speech-to-text failed: {e}")
                    return
                if text:
                    self.on_final("local-%d" % int(time.time() * 1000), text)

    # ---- events from the recognition ------------------------------------------------------------------
    def on_speech_start(self):
        if self.headphones and self.interrupt_on_speech:
            self.voice.interrupt()

    def on_partial(self, item, text):
        try:
            result = self.c.handle_partial(item, text)
        except Exception as e:
            self.log(f"  partial failed: {e}")
            return
        if result is None:
            return
        if hasattr(result, "status"):
            self.log(f'  "{text}" (partial) -> {result.status} [{result.intent}]')
            self.last_outcome = result
            return
        if self.speculative and self.c.persona is not None:
            self._speculate(item, text, result)

    def _speculate(self, item, text, need):
        rec = {"text": text, "answer": None}
        with self._lock:
            self._spec[item] = rec

        def run():
            try:
                rec["answer"] = self.c.persona.respond(need.transcript, self.c.intents, [i.id for i in self.c.intents if i.improvise],
                                                       need.state, need.history)
            except Exception as e:
                rec["answer"] = (e, need.plain)
        rec["thread"] = threading.Thread(target=run, daemon=True)
        rec["thread"].start()

    def on_final(self, item, text):
        with self._lock:
            spec = self._spec.pop(item, None)
        kind, result = self.c.prepare(text) if not self.c._consumed.get(item) else ("outcome", self.c.handle_final(item, text))
        if kind == "outcome":
            self.last_outcome = result
            self.log(f'  "{text}" -> {result.status}' + (f" [{result.intent}]" if result.intent else ""))
            return result
        if kind == "llm":
            o = self.c.finish_llm(text, result)
            self.last_outcome = o
            return o
        need = result
        if spec is not None and spec.get("thread") is not None and self.c.speculation_fits(spec["text"], text):
            spec["thread"].join(8.0)
            if spec["answer"] is not None and not isinstance(spec["answer"], tuple):
                o = self.c.finish_persona(text, spec["answer"])
                o.extra["speculative"] = True
                self.last_outcome = o
                self.log(f'  "{text}" -> {o.status} (speculative)')
                return o
        return self._stream_answer(text, need)

    def _stream_answer(self, text, need):
        c = self.c
        stream = SentenceStream()
        started = {"v": False}
        spoken = {"text": ""}

        def speak_sentence(sentence, meta, final):
            if sentence:
                spoken["text"] += (" " if spoken["text"] else "") + sentence
                stream.push(sentence + " ")
                if not started["v"] and self.ws is not None:
                    started["v"] = True
                    gen = self.ws.say_stream(iter(stream), meta, stop=lambda: self.voice._stop)
                    self.voice.say(None, meta.get("stage", 0), meta, stream=_fallback_line(gen, lambda: spoken["text"]))
            if final:
                stream.close()
                if not started["v"] and spoken["text"]:
                    self.voice.say(spoken["text"], meta.get("stage", 0), meta)     # no websocket voice: the ordinary one
        try:
            events = c.persona.respond_stream(need.transcript, c.intents, [i.id for i in c.intents if i.improvise], need.state, need.history)
        except Exception:
            events = iter(())
        o = c.handle_streamed(text, events, speak_sentence, need)
        self.last_outcome = o
        self.log(f'  "{text}" -> {o.status}' + (f" [{o.intent}]" if o.intent else "") + (" (streamed)" if o.extra.get("streamed") else ""))
        return o


def _fallback_line(gen, whole_text):
    """a chunk stream that, failing before any sound, raises TtsError so that Voice speaks the whole line instead"""
    got = False
    try:
        for chunk in gen:
            got = True
            yield chunk
    except TtsError:
        if got:
            return
        raise


class ConversationManager:
    """holds the loops by mode name; switch(mode) stops the current one and starts the next"""

    def __init__(self, loops, mode="classic", on_switch=None, log=print):
        self.loops, self.mode, self.on_switch, self.log = loops, mode, on_switch, log
        self.current = None
        self.order = [m for m in ("classic", "live", "agents") if m in loops]

    def start(self):
        self.switch(self.mode, announce=False)

    def switch(self, mode, announce=True):
        if mode not in self.loops:
            self.log(f"  mode {mode}: not available")
            return False
        if self.current is not None:
            try:
                self.current.stop()
            except Exception as e:
                self.log(f"  stopping {self.mode}: {e}")
        self.mode, self.current = mode, self.loops[mode]
        self.current.start()
        self.log(f"  conversation mode: {mode}")
        if announce and self.on_switch:
            self.on_switch(mode)
        return True

    def next(self):
        i = self.order.index(self.mode) if self.mode in self.order else -1
        return self.switch(self.order[(i + 1) % len(self.order)])
