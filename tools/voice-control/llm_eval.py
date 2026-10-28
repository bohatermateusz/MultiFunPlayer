"""`voice_control.py eval [file]`: runs a list of utterances through the real controller (with the real
LLM, dry run: nothing is sent to the machine) and prints how well it guessed. Needs the API key."""
import json
import os
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CASES = os.path.join(HERE, "tests", "llm_cases.json")


def run_eval(controller, path=None, out=sys.stdout):
    with open(path or DEFAULT_CASES, encoding="utf-8") as f:
        cases = json.load(f)
    controller.dry_run = True
    controller.log_path = None
    right, wrong, false_triggers, latencies, llm_calls = 0, [], [], [], 0
    for case in cases:
        controller._last.clear()
        controller._recent.clear()
        controller._pending = None
        outcome = controller.handle(case["say"])
        got = outcome.intent if outcome.status in ("dry-run", "executed") else "none"
        if outcome.via == "llm":
            llm_calls += 1
            if "latency_ms" in outcome.extra:
                latencies.append(outcome.extra["latency_ms"])
        expected = case["intent"]
        if got == expected:
            right += 1
        else:
            (false_triggers if expected == "none" else wrong).append((case["say"], expected, got, outcome.detail))
    total = len(cases)
    print(f"{right}/{total} right ({100 * right / total:.0f}%), {llm_calls} utterances went to the model", file=out)
    if latencies:
        print(f"model latency: median {statistics.median(latencies):.0f} ms, max {max(latencies)} ms", file=out)
    print(f"FALSE TRIGGERS (a command ran that should not have): {len(false_triggers)}", file=out)
    for say, expected, got, detail in false_triggers:
        print(f'  "{say}" -> {got}', file=out)
    print(f"missed or wrong command: {len(wrong)}", file=out)
    for say, expected, got, detail in wrong:
        print(f'  "{say}": wanted {expected}, got {got} {detail}', file=out)
    return 1 if false_triggers else 0
