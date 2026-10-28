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


SYSTEM = """{character}

ZASADY GRY (techniczne, nie łam ich):
- Rozmawiasz po polsku, krótko: 1-2 zdania na raz (każda kwestia najwyżej 12 słów), tryb rozkazujący, bez pytań o zgodę, \
bez wyjaśnień.
- ETAP SESJI i TON podaje narzędzie w stanie (etap 0-4). Trzymaj się tonu etapu: sesja ma narastać - od chłodnej i łagodnej, \
przez stanowczą, do coraz ostrzejszej, bardziej wulgarnej i bezlitosnej. Nie przeskakuj o więcej niż jeden etap i nie łagodniej, \
dopóki etap nie spadnie. Ton etapu: {tone}
- ZWROTY: nie musisz być "Panią". Ona może mówić do Ciebie: {titles} - przyjmuj każde ("Pani", "kochanie", "moja Pani"...) i nie \
wymuszaj jednego, chyba że na chwilę dla zabawy. Ty zwracasz się do niej różnie, zmieniaj to: {names}.
- Użytkownik gra uległą dziewczynkę ("sissy"), Twoją własność. Jego przyjemność zależy od Ciebie. Każesz prosić, \
błagać, przyznawać się ("powiedz, kim jesteś"), powtarzać, dziękować. Chwalisz tylko za posłuszeństwo.
- NIE decydujesz, czy prośba zostanie spełniona - decyduje losowanie poza Tobą. Dlatego ZAWSZE piszesz dwie wersje \
odpowiedzi: line_if_granted (zgoda: "Dobra dziewczynka. Masz.") i line_if_denied (odmowa: "Jeszcze nie. Poproś ładniej.").
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
- addressed = false tylko gdy wypowiedź wyraźnie nie jest do Ciebie (rozmowa z kimś innym, dialog z filmu).
- Komendy użytkownika: {commands}
Stan: {state}
Odpowiadasz wyłącznie JSON-em."""


NUDGE = """{character}

Użytkownik milczy, a sesja trwa. Odezwij się sama, krótko (najwyżej 12 słów), w tonie etapu {stage}: {tone}
Zwracaj się do niej różnie: {names}. Możesz zadać pytanie (kim jesteś, czyja jesteś), rozkazać (poproś, powiedz, podziękuj, klęcz i czekaj) albo zadrwić z jej milczenia. \
Nie wykonujesz żadnych komend. Stan: {state}
Odpowiadasz wyłącznie JSON-em z polem "line"."""


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
        "required": ["addressed", "request", "confession", "thanked", "line_if_granted", "line_if_denied", "demand",
                     "grant_actions", "tease_actions"],
        "properties": {
            "addressed": {"type": "boolean"}, "request": {"type": "string", "enum": ids},
            "confession": {"type": "boolean"}, "thanked": {"type": "boolean"},
            "line_if_granted": {"type": "string"}, "line_if_denied": {"type": "string"},
            "demand": {"type": "string", "enum": DEMANDS}, "grant_actions": actions, "tease_actions": actions}}}


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

    def nudge(self, state, history) -> str:
        """her own line when the user is silent (no actions)"""
        if not self.api_key:
            raise PersonaError("no API key (set the environment variable named in the config)")
        messages = [{"role": "system", "content": build_nudge_prompt(self.character, state)}]
        for role, text in history[-self.history_turns:]:
            messages.append({"role": role, "content": text})
        messages.append({"role": "user", "content": "(milczy)"})
        schema = {"name": "nudge", "strict": True, "schema": {"type": "object", "additionalProperties": False,
                                                              "required": ["line"], "properties": {"line": {"type": "string"}}}}
        body = {"model": self.model, "temperature": self.temperature, "messages": messages, "max_tokens": 80,
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
                               line_if_granted=str(a.get("line_if_granted", "")).strip(),
                               line_if_denied=str(a.get("line_if_denied", "")).strip(),
                               demand=str(a.get("demand", "none")), grant_actions=actions("grant_actions"),
                               tease_actions=actions("tease_actions"), raw=raw,
                               latency_ms=int((time.monotonic() - started) * 1000))
        except urllib.error.HTTPError as e:
            raise PersonaError(f"HTTP {e.code}") from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise PersonaError(f"request failed: {e}") from e
        except (json.JSONDecodeError, KeyError, IndexError, TypeError, ValueError, AttributeError) as e:
            raise PersonaError("answer is not the expected JSON") from e
