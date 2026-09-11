"""Game corpus and coverage measurement.

"Adapts to most games" must be a number, not a claim. The corpus lists the
axes each game needs; ``coverage_report`` runs ``capability_check`` over it and
produces the failure histogram that decides which axis to build next.
"""
from __future__ import annotations

import json
from collections import Counter
from functools import lru_cache
from pathlib import Path
from typing import Any

from .capability import capability_check

CORPUS_PATH = Path(__file__).with_name("data") / "corpus.json"


@lru_cache(maxsize=1)
def load_corpus() -> tuple[dict[str, Any], ...]:
    games = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))
    ids = [game["id"] for game in games]
    if len(ids) != len(set(ids)):
        raise ValueError("corpus_duplicate_game_id")
    for game in games:
        if not game.get("required_axes"):
            raise ValueError(f"corpus_game_without_axes:{game['id']}")
    return tuple(games)


def coverage_report() -> dict[str, Any]:
    reports: list[dict[str, Any]] = []
    histogram: Counter[str] = Counter()
    for game in load_corpus():
        check = capability_check(game["required_axes"])
        reports.append({"id": game["id"], "name": game["name"], "category": game["category"],
                        "required_axes": check.required_axes, "expressible": check.expressible,
                        "missing": check.missing, "builtin": game.get("builtin")})
        for gap in check.missing:
            histogram[gap["axis"]] += 1
    total = len(reports)
    covered = sum(1 for report in reports if report["expressible"])
    return {"total": total, "covered": covered,
            "coverage": round(covered / total, 4) if total else 0.0,
            "missing_histogram": dict(sorted(histogram.items(), key=lambda item: (-item[1], item[0]))),
            "games": reports}
