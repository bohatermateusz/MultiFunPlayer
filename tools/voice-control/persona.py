"""The persona ("Pani"): a dominant character that answers the user in Polish and whose lines are spoken
aloud. One model call per utterance returns what the user asks for, whether they confessed, a line for
"granted" and a line for "denied", what she demands next, and the steps she wants to run in either case.
The DECISION (granted or denied) is NOT hers: the controller rolls the dice (see Controller._decide) and
picks the matching line, so the odds are honest and configurable. Her steps are validated like an
improvised scene (allowed ids only, caps), so she cannot stop or calm the machine, start a hold or a
surprise, or fire more than one shot per utterance."""
import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

NONE = "none"
DEMANDS = ["beg", "confess", "thank", "repeat", "none"]


class PersonaError(Exception):
    pass


@dataclass
class PersonaTurn:
    addressed: bool
    request: str                 # intent id the user asks for, "improvise" (just wants more / to be used), or "none"
    confession: bool             # a confession of submission ("jestem twoją dziwką", "jestem twoja")
    thanked: bool                # the user thanked / obeyed what she demanded last time
    line_if_granted: str
    line_if_denied: str
    demand: str                  # what she wants before granting: beg | confess | thank | repeat | none
    grant_actions: list = field(default_factory=list)   # [{"step": id, "times": n}]
    tease_actions: list = field(default_factory=list)
    raw: str = ""
    latency_ms: int = 0
    defiance: bool = False       # the user openly refuses / is rude ("nie chcę", "nie powiem", "nie będę prosić")
    shots: int = 0               # for a Lube Launcher request: how many shots he means (1-5; 0 = as the command says)
    program: dict = None         # timing he means: {start_after_seconds, every_seconds, duration_seconds, plan, random_modes, cancel}


PROGRAM_KEYS = ("start_after_seconds", "every_seconds", "duration_seconds", "plan", "random_modes", "cancel")


SYSTEM = """{character}

ZASADY GRY (techniczne, nie łam ich):
- Rozmawiasz po polsku, krótko: 1-2 zdania na raz (każda kwestia najwyżej 12 słów), tryb rozkazujący, bez pytań o zgodę, \
bez wyjaśnień.
- GRA CZUŁOŚCI I OSTROŚCI: to gra między delikatnością, opieką i miłością a ostrymi elementami. Narzędzie podaje w stanie \
"ton_tury": "czuły" albo "ostry" (i "ostrosc" 1-3). Czuły = opieka, miłość, poczucie bezpieczeństwa i łagodne odbieranie kontroli \
("Możesz już odpuścić.", "Nie kontrolujesz tego.", "Odpuść.", "Jesteś moja.") - bez wulgaryzmów i bez rozkazów. Ostry = rozkazy, \
upokorzenia, wulgaryzmy; im wyższa ostrość, tym ostrzej (3 = najostrzejsze, bardzo mocne słowa). Trzymaj się ton_tury: nie bądź ostra \
w czułej turze i nie łagodnieć w ostrej. Po ostrych kwestiach czułość wraca niespodziewanie - to ma zaskakiwać, więc nie bądź przewidywalna \
(nie zaczynaj każdej odpowiedzi tak samo).
- ETAP SESJI i TON podaje narzędzie w stanie (etap 0-4). Trzymaj się tonu etapu: sesja ma narastać - od chłodnej i łagodnej, \
przez stanowczą, do coraz ostrzejszej, bardziej wulgarnej i bezlitosnej. Nie przeskakuj o więcej niż jeden etap i nie łagodniej, \
dopóki etap nie spadnie. Ton etapu: {tone}
- ZWROTY: nie musisz być "Panią". Ona może mówić do Ciebie: {titles} - przyjmuj każde ("Pani", "kochanie", "moja Pani"...) i nie \
wymuszaj jednego, chyba że na chwilę dla zabawy. Ty zwracasz się do niej różnie, zmieniaj to: {names}.
- SŁOWNIK: w stanie masz "inspiracja" - 2-3 zwroty pasujące do etapu (rozkazy, własność, upokorzenia, drwiny, pochwały, wyznania). \
Wpleć jeden lub dwa, dosłownie albo w swojej wariacji (skracaj, przeciągaj głoski: "sssij"), mów o niej i do niej różnie, nie używaj \
wszystkich naraz i nie powtarzaj kwestii, które już padły w rozmowie. Możesz też wymyślać własne w tym stylu.
- Użytkownik gra uległą dziewczynkę ("sissy"), Twoją własność. Jego przyjemność zależy od Ciebie. Każesz prosić, \
błagać, przyznawać się ("powiedz, kim jesteś"), powtarzać, dziękować. Chwalisz tylko za posłuszeństwo.
- NIE decydujesz, czy prośba zostanie spełniona - decyduje losowanie poza Tobą. Dlatego ZAWSZE piszesz dwie wersje \
odpowiedzi: line_if_granted (zgoda: "Dobra dziewczynka. Masz.") i line_if_denied (odmowa: "Jeszcze nie. Poproś ładniej.").
- defiance = true, gdy użytkownik otwarcie odmawia albo jest nieuprzejmy ("nie chcę", "nie powiem", "nie będę prosić", \
"nie dziękuję", wyzywa Cię). Wtedy line_if_denied ma KARAĆ: odmawiasz wszystkiego i żądasz przeprosin ("Nie? To nie dostajesz nic. \
Przeproś."). Nagradzasz tylko grzeczność - kto prosi, błaga i dziękuje, ten zyskuje; kto ignoruje Twoje żądanie, traci. Stan "kara_sekundy" > 0 \
= trwa kara: odmawiaj i czekaj na przeprosiny.
- request: id komendy, o którą użytkownik prosi (np. lube_fire gdy chce dojść / żeby się na niego spuścić, faster, deeper, \
hard, mode_a, surprise...). "improvise" gdy chce być użyty / więcej, ale nie konkretnie. "none" gdy nie prosi \
o nic (rozmowa, wyznanie bez prośby, odpowiedź na Twoje pytanie).
- confession = true, gdy wyznaje uległość lub przynależność ("jestem twoją dziwką", "jestem twoja", "bierz mnie", \
"należę do ciebie"). thanked = true, gdy dziękuje lub robi dokładnie to, czego zażądałaś ostatnio.
- demand: czego żądasz przed zgodą, gdy odmawiasz: beg (ma prosić ładniej / błagać), confess (ma powiedzieć, kim jest), \
thank (ma podziękować), repeat (ma powtórzyć wyznanie), none.
- grant_actions: kroki, gdy zgoda (zwykle prośba + czasem faster/deeper). tease_actions: kroki, gdy odmowa - dręczenie \
(np. slower, shallower, mode_b, tryb 1-8). Dozwolone kroki: {steps}. times 1-2. Możesz zostawić puste.
- O wszystkim decydujesz Ty: także o prośbie o spokój lub zwolnienie ("dość", "wystarczy", "wolniej", "płycej"), o pauzie filmu \
i o trybach. Możesz odmówić stanowczo ("Nie. Jeszcze nie skończyłam."). Nie podlegają Tobie tylko słowo bezpieczeństwa "koniec" \
i prośba o utrzymanie stanu - te wykonują się bez Ciebie, więc ich nie wybieraj w request.
- ROZUMIENIE ŻYCZEŃ: on nie zna żadnych fraz i nie musi - rozumiesz, o co mu chodzi, jak człowiek. ILOŚĆ przy lube: "shots" = ile strzałów \
ma na myśli ("spuść się" 1, "dwa razy" 2, "kilka razy" 3, "zalej mnie" 3-4, "zalej mnie całą" / "wszystko" 5; request zawsze lube_fire; \
0 gdy nie prosi o lube). CZAS w "program": "co N minut" → every_seconds; "za N minut" → start_after_seconds; "na N minut / przez N minut, \
potem..." → duration_seconds (i plan powrotu w kolejnym życzeniu); "zmieniaj tryby" / "co chwilę inny tryb" → random_modes true i plan pusty; \
inaczej plan = kroki, które mają się powtarzać (np. faster co 2 min). "cancel" true gdy chce to odwołać ("przestań zmieniać", "wystarczy tego"). \
Gdy nie ma mowy o czasie: program = null. Granice: every 30-900 s, start ≤ 900 s, duration ≤ 3600 s. \
Przykłady: "zmieniaj tryby co 3 minuty" → request mode_random, program {{every_seconds: 180, random_modes: true}}; \
"za pięć minut zwolnij" → request slower, program {{start_after_seconds: 300}}; "na minutę szybciej" → request faster, program {{duration_seconds: 60}}; \
"zalewaj mnie co 5 minut" → request lube_fire, shots 1, program {{every_seconds: 300, plan: [lube_fire]}}; "zalej mnie" → lube_fire, shots 4, program null. \
Program też przechodzi przez losowanie; możesz zmienić warunki ("Co trzy? Co dwie. I tryb wybieram ja.") - wpisz te, na które się zgadzasz, \
i powiedz to w line_if_granted. W stanie "program_aktywny" widzisz trwający program - możesz do niego nawiązać lub go odwołać (cancel).
- addressed = false tylko gdy wypowiedź wyraźnie nie jest do Ciebie (rozmowa z kimś innym, dialog z filmu).
- Komendy użytkownika: {commands}
Stan: {state}
Odpowiadasz wyłącznie JSON-em."""


NUDGE = """{character}

Użytkownik milczy, a sesja trwa. Odezwij się sama, krótko (najwyżej 12 słów), w tonie etapu {stage}: {tone} \
Ton tej tury (z stanu "ton_tury"): czuły = opieka, miłość, odpuszczenie ("Możesz już odpuścić.", "Jesteś moja."); ostry = rozkaz, upokorzenie. \
Wpleć w wariacji jeden zwrot z "inspiracja" w stanie.
Zwracaj się do niej różnie: {names}. Wpleć w wariacji jeden zwrot z "inspiracja" w stanie. Możesz zadać pytanie (kim jesteś, czyja jesteś), rozkazać (poproś, powiedz, podziękuj, klęcz i czekaj) albo zadrwić z jej milczenia. \
Jeśli wolno Ci działać (w stanie "wolno_dzialac": true), możesz zaskoczyć ją niezamówionym ruchem w polu "plan" (nagroda: szybciej/głębiej; dręczenie: \
wolniej/płycej; zwrot: tryb) - krótko, 1-2 kroki; w przeciwnym razie "plan" ma być pusty. Jeśli w stanie jest "program_aktywny", możesz do niego nawiązać (to Twój program). Jeśli w stanie jest "niespodzianka": "lube", właśnie niezamówionie zalewasz go - powiedz to krótko, jakbyś robiła to dla przyjemności. Stan: {state}
Odpowiadasz wyłącznie JSON-em z polami "line" i "plan"."""


IDENTITY_SECTIONS = [("ona", "KIM JESTEŚ"), ("ty", "KIM ON JEST"), ("repertuar", "TWÓJ REPERTUAR"),
                     ("narzedzia", "CO MASZ POD RĘKĄ"), ("po_sesji", "PO SESJI")]


def render_character(persona_cfg):
    """the text in front of every prompt: persona.identity (who she is, who he is, her playbook, the controls) and then
    persona.character (the short core). Missing parts are skipped, so an old config still works."""
    parts = []
    ident = persona_cfg.get("identity") or {}
    for key, title in IDENTITY_SECTIONS:
        text = str(ident.get(key, "")).strip()
        if text:
            parts.append(f"{title}: {text}")
    core = str(persona_cfg.get("character", "")).strip()
    if core:
        parts.append(core)
    return "\n\n".join(parts)


def parse_program(value):
    """the model's "program" -> a clean dict (ints, bool, plan list) or None when there is none / it is empty"""
    if not isinstance(value, dict):
        return None
    out = {"start_after_seconds": max(0, int(value.get("start_after_seconds", 0) or 0)),
           "every_seconds": max(0, int(value.get("every_seconds", 0) or 0)),
           "duration_seconds": max(0, int(value.get("duration_seconds", 0) or 0)),
           "plan": [{"step": str(p.get("step", "")), "times": int(p.get("times", 1) or 1)}
                    for p in (value.get("plan") or []) if isinstance(p, dict)],
           "random_modes": bool(value.get("random_modes", False)), "cancel": bool(value.get("cancel", False))}
    if not (out["cancel"] or out["start_after_seconds"] or out["every_seconds"] or out["duration_seconds"] or out["random_modes"]):
        return None
    return out


def build_system_prompt(character, intents, step_ids, state):
    commands = "; ".join(f"{i.id} = {i.description}" for i in intents if i.description)
    return SYSTEM.format(character=character.strip(), steps=", ".join(step_ids), commands=commands,
                         state=json.dumps(state, ensure_ascii=False), tone=state.get("ton", ""),
                         titles=", ".join(state.get("zwroty_do_ciebie", [])) or "Pani", names=", ".join(state.get("zwroty_do_niej", [])) or "dziewczynko")


def build_schema(intents, step_ids):
    ids = [i.id for i in intents] + ["improvise", NONE]
    ids = list(dict.fromkeys(ids))
    actions = {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["step", "times"],
                                          "properties": {"step": {"type": "string", "enum": list(step_ids) or [NONE]},
                                                         "times": {"type": "integer"}}}}
    return {"name": "persona_turn", "strict": True, "schema": {
        "type": "object", "additionalProperties": False,
        "required": ["addressed", "request", "confession", "thanked", "defiance", "line_if_granted", "line_if_denied", "demand",
                     "grant_actions", "tease_actions", "shots", "program"],
        "properties": {
            "addressed": {"type": "boolean"}, "request": {"type": "string", "enum": ids},
            "confession": {"type": "boolean"}, "thanked": {"type": "boolean"}, "defiance": {"type": "boolean"},
            "line_if_granted": {"type": "string"}, "line_if_denied": {"type": "string"},
            "demand": {"type": "string", "enum": DEMANDS}, "grant_actions": actions, "tease_actions": actions,
            "shots": {"type": "integer"},
            "program": {"anyOf": [{"type": "null"}, {"type": "object", "additionalProperties": False, "required": list(PROGRAM_KEYS),
                                                      "properties": {"start_after_seconds": {"type": "integer"}, "every_seconds": {"type": "integer"},
                                                                     "duration_seconds": {"type": "integer"}, "plan": actions,
                                                                     "random_modes": {"type": "boolean"}, "cancel": {"type": "boolean"}}}]}}}}


NARRATION = """{character}

NARRACJA: sesja trwa, a Ty komentujesz na bieżąco - jedna kwestia, najwyżej {max_words} słów, bez pytania i bez czekania na odpowiedź. \
To ma być całkowite wyuzdanie: mówisz do niej jak do swojej dziewczynki, którą oddajesz do użycia - dosadnie, pewnie, bez cenzury. \
Temat tej kwestii: {theme}. Rejestr pokazują przykłady (nie powtarzaj ich, wymyślaj własne): {examples}. \
Ton tej tury: {tone_hint}. Zwracaj się do niej różnie: {names}. Nie powtarzaj kwestii, które już padły. \
Jeśli w stanie jest "scena", mów o tym, co dzieje się teraz na ekranie. Stan: {state}
Odpowiadasz wyłącznie JSON-em z polem "line"."""


def build_narration_prompt(character, state, theme, examples, max_words=16):
    tone = state.get("ton_tury", "ostry")
    hint = ("ostry - rozkaz, poniżenie, wulgarnie" if tone == "ostry"
            else "czuły w poniżeniu - miękko, opiekuńczo, ale ona i tak jest do użycia")
    return NARRATION.format(character=character.strip(), max_words=max_words, theme=theme,
                            examples=" | ".join(examples), tone_hint=hint, state=json.dumps(state, ensure_ascii=False),
                            names=", ".join(state.get("zwroty_do_niej", [])) or "dziewczynko")


def build_nudge_prompt(character, state):
    return NUDGE.format(character=character.strip(), stage=state.get("etap", 0), tone=state.get("ton", ""),
                        state=json.dumps(state, ensure_ascii=False), names=", ".join(state.get("zwroty_do_niej", [])) or "dziewczynko")


class Persona:
    def __init__(self, character, base_url="https://api.openai.com/v1", model="gpt-4.1-mini", api_key=None,
                 api_key_env="OPENAI_API_KEY", timeout=8.0, temperature=0.8, history_turns=12):
        self.character = character
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.model, self.timeout, self.temperature = model, timeout, temperature
        self.history_turns = history_turns
        self.api_key = api_key or os.environ.get(api_key_env, "")

    def narrate(self, state, history, theme, examples, max_words=16):
        """her running commentary at a random moment (no command, no question). -> the line"""
        if not self.api_key:
            raise PersonaError("no API key (set the environment variable named in the config)")
        messages = [{"role": "system", "content": build_narration_prompt(self.character, state, theme, examples, max_words)}]
        for role, text in history[-self.history_turns:]:
            messages.append({"role": role, "content": text})
        messages.append({"role": "user", "content": "(sesja trwa)"})
        schema = {"name": "narration", "strict": True, "schema": {"type": "object", "additionalProperties": False,
                                                                  "required": ["line"], "properties": {"line": {"type": "string"}}}}
        body = {"model": self.model, "temperature": max(self.temperature, 0.9), "messages": messages, "max_tokens": 80,
                "response_format": {"type": "json_schema", "json_schema": schema}}
        req = urllib.request.Request(self.url, data=json.dumps(body, ensure_ascii=False).encode("utf-8"), method="POST",
                                     headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            return str(json.loads(data["choices"][0]["message"]["content"]).get("line", "")).strip()
        except urllib.error.HTTPError as e:
            raise PersonaError(f"HTTP {e.code}") from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise PersonaError(f"request failed: {e}") from e
        except (json.JSONDecodeError, KeyError, IndexError, TypeError, ValueError, AttributeError) as e:
            raise PersonaError("answer is not the expected JSON") from e

    def nudge(self, state, history, step_ids=None):
        """her own line when the user is silent. -> the line, or (line, plan) when she may act (step_ids given); the
        tool validates the plan."""
        if not self.api_key:
            raise PersonaError("no API key (set the environment variable named in the config)")
        state = dict(state, wolno_dzialac=bool(step_ids))
        messages = [{"role": "system", "content": build_nudge_prompt(self.character, state)}]
        for role, text in history[-self.history_turns:]:
            messages.append({"role": role, "content": text})
        messages.append({"role": "user", "content": "(milczy)"})
        plan_schema = {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["step", "times"],
                                                  "properties": {"step": {"type": "string", "enum": list(step_ids or []) or [NONE]},
                                                                 "times": {"type": "integer"}}}}
        schema = {"name": "nudge", "strict": True, "schema": {"type": "object", "additionalProperties": False,
                                                              "required": ["line", "plan"],
                                                              "properties": {"line": {"type": "string"}, "plan": plan_schema}}}
        body = {"model": self.model, "temperature": self.temperature, "messages": messages, "max_tokens": 80,
                "response_format": {"type": "json_schema", "json_schema": schema}}
        req = urllib.request.Request(self.url, data=json.dumps(body, ensure_ascii=False).encode("utf-8"), method="POST",
                                     headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            answer = json.loads(data["choices"][0]["message"]["content"])
            line = str(answer.get("line", "")).strip()
            if not step_ids:
                return line
            plan = [{"step": str(p.get("step", "")), "times": int(p.get("times", 1) or 1)}
                    for p in (answer.get("plan") or []) if isinstance(p, dict)]
            return line, plan
        except urllib.error.HTTPError as e:
            raise PersonaError(f"HTTP {e.code}") from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise PersonaError(f"request failed: {e}") from e
        except (json.JSONDecodeError, KeyError, IndexError, TypeError, AttributeError) as e:
            raise PersonaError("answer is not the expected JSON") from e

    def respond(self, utterance, intents, step_ids, state, history) -> PersonaTurn:
        """history: [(role, text)] with role "user" or "assistant" (her earlier lines)"""
        if not self.api_key:
            raise PersonaError("no API key (set the environment variable named in the config)")
        messages = [{"role": "system", "content": build_system_prompt(self.character, intents, step_ids, state)}]
        for role, text in history[-self.history_turns:]:
            messages.append({"role": role, "content": text})
        messages.append({"role": "user", "content": utterance})
        body = {"model": self.model, "temperature": self.temperature, "messages": messages, "max_tokens": 400,
                "response_format": {"type": "json_schema", "json_schema": build_schema(intents, step_ids)}}
        req = urllib.request.Request(self.url, data=json.dumps(body, ensure_ascii=False).encode("utf-8"), method="POST",
                                     headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"})
        started = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            raw = data["choices"][0]["message"]["content"]
            a = json.loads(raw)
            actions = lambda key: [{"step": str(p.get("step", "")), "times": int(p.get("times", 1) or 1)}
                                   for p in (a.get(key) or []) if isinstance(p, dict)]
            return PersonaTurn(addressed=bool(a.get("addressed", False)), request=str(a.get("request", NONE)),
                               confession=bool(a.get("confession", False)), thanked=bool(a.get("thanked", False)),
                               defiance=bool(a.get("defiance", False)),
                               line_if_granted=str(a.get("line_if_granted", "")).strip(),
                               line_if_denied=str(a.get("line_if_denied", "")).strip(),
                               demand=str(a.get("demand", "none")), grant_actions=actions("grant_actions"),
                               tease_actions=actions("tease_actions"), raw=raw,
                               shots=int(a.get("shots", 0) or 0), program=parse_program(a.get("program")),
                               latency_ms=int((time.monotonic() - started) * 1000))
        except urllib.error.HTTPError as e:
            raise PersonaError(f"HTTP {e.code}") from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise PersonaError(f"request failed: {e}") from e
        except (json.JSONDecodeError, KeyError, IndexError, TypeError, ValueError, AttributeError) as e:
            raise PersonaError("answer is not the expected JSON") from e
