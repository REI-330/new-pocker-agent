"""Generation benchmark: natural language -> a verified, playable game.

Measures the loop, not the tools: how often a goal becomes a *playtested* game,
how many repair turns it takes, and what it reports when the host cannot express
the game. Two modes:

- ``--scripted``: a deterministic stand-in model, so the harness itself is
  testable in CI without network or credentials.
- default: the saved model configuration (a real run), writing a report to
  ``artifacts/generation-report.json``.

Run:
    uv run python scripts/benchmark_generation.py --scripted
    uv run python scripts/benchmark_generation.py
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from pocker_agent.agent import run_loop

ROOT = Path(__file__).resolve().parents[1]
CASES_PATH = ROOT / "benchmarks" / "generation_cases.json"

IR_TEMPLATES: dict[str, dict] = {
    "arithmetic": {"kind": "arithmetic", "game_id": "bench-arithmetic", "title": "24点练习",
                   "max_rounds": 3, "target": 24},
    "war": {"kind": "war", "game_id": "bench-war", "title": "比大小", "max_rounds": 5},
    "shedding": {"kind": "shedding", "game_id": "bench-eights", "title": "疯狂八",
                 "hand_size": 5, "wild_rank": "8"},
    "whist": {"kind": "whist", "game_id": "bench-whist", "title": "Whist", "cards_each": 5},
    "poker": {"kind": "poker", "game_id": "bench-poker", "title": "单轮摊牌",
              "stacks": 100, "min_raise": 10},
    "blackjack": {"kind": "blackjack", "game_id": "bench-blackjack", "title": "21点",
                  "max_rounds": 3},
}


def load_cases(path: Path | None = None) -> list[dict]:
    return json.loads((path or CASES_PATH).read_text(encoding="utf-8"))


def decision(tool: str, **args) -> str:
    return json.dumps({"tool": tool, "args": args})


class ScriptedModel:
    """Deterministic stand-in: the canonical path for each expected outcome."""

    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.calls = 0

    def complete(self, messages, **kwargs):
        if self.calls >= len(self.responses):
            raise RuntimeError("scripted responses exhausted")
        response = self.responses[self.calls]
        self.calls += 1
        return response


def responses_for(case: dict) -> list[str]:
    if case["expect"] == "unsupported":
        return [decision("unsupported", message=f"当前实现缺少 {case.get('missing', [])}",
                         missing=case.get("missing", []))]
    template = dict(IR_TEMPLATES[case["expect"]])
    template["game_id"] = f"bench-{case['id']}"
    return [decision("propose_ir", ir=template), decision("compose_plan"),
            decision("playtest"), decision("finalize")]


def _missing_axes(observation: dict) -> list[str]:
    """Gap names may arrive as strings (unsupported) or dicts (capability)."""
    values = observation.get("missing") or []
    return [value if isinstance(value, str) else value.get("axis", "") for value in values]


def run_case(case: dict, model_factory) -> dict:
    model = model_factory(responses_for(case))
    result = run_loop(case["goal"], model)
    playtest = result.playtest or {}
    missing = [axis for observation in result.observations
               for axis in _missing_axes(observation)]
    return {"id": case["id"], "goal": case["goal"], "expected": case["expect"],
            "kind": result.kind, "finalized": result.finalized, "attempts": result.attempts,
            "playtest_ok": bool(playtest.get("ok")),
            "missing": missing,
            "errors": [observation.get("error") for observation in result.observations
                       if not observation.get("ok")]}


def benchmark(cases: list[dict], model_factory) -> dict:
    records = [run_case(case, model_factory) for case in cases]
    playable = [record for record in records if record["expected"] != "unsupported"]
    boundaries = [record for record in records if record["expected"] == "unsupported"]
    finalized = [record for record in playable if record["finalized"]]
    playtested = [record for record in finalized if record["playtest_ok"]]
    correctly_unsupported = [record for record in boundaries if record["kind"] == "unsupported"]
    histogram = Counter(axis for record in records for axis in record["missing"])
    return {
        "total": len(records),
        "playable_cases": len(playable),
        "finalized": len(finalized),
        "playtest_passed": len(playtested),
        "success_rate": round(len(finalized) / len(playable), 3) if playable else 0.0,
        "playtest_pass_rate": round(len(playtested) / len(finalized), 3) if finalized else 0.0,
        "unsupported_cases": len(boundaries),
        "correctly_unsupported": len(correctly_unsupported),
        "mean_attempts": round(sum(record["attempts"] for record in records) / len(records), 2)
        if records else 0.0,
        "missing_histogram": dict(sorted(histogram.items(), key=lambda item: (-item[1], item[0]))),
        "cases": records,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scripted", action="store_true",
                        help="deterministic harness check; no model needed")
    parser.add_argument("--output", default="artifacts/generation-report.json")
    args = parser.parse_args()

    if args.scripted:
        model_factory = ScriptedModel
    else:
        from pocker_agent.configuration import ConfigStore
        from pocker_agent.llm import OpenAICompatibleClient

        saved = ConfigStore().read()
        if not saved.public()["configured"]:
            raise SystemExit("未配置模型；先用 --scripted 验证基准本身，或先保存模型配置")
        client = OpenAICompatibleClient.from_config(saved)

        def model_factory(responses):          # a real model ignores the script
            return client

    report = benchmark(load_cases(), model_factory)
    output = ROOT / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"cases {report['total']} | finalized {report['finalized']}/{report['playable_cases']} "
          f"| playtest pass {report['playtest_passed']} "
          f"| correctly unsupported {report['correctly_unsupported']}/{report['unsupported_cases']} "
          f"| mean attempts {report['mean_attempts']}")
    print("missing axes:", report["missing_histogram"])
    print("report:", output)
    failures = [record for record in report["cases"]
                if (record["expected"] != "unsupported" and not record["finalized"])
                or (record["expected"] == "unsupported" and record["kind"] != "unsupported")]
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
