"""Golden traces captured before the v0.2 engines were deleted.

Per engineering-standards section 14.3 a migrated family must keep rule samples
and a captured trace, and must NOT inherit the old engine's defects. These
fixtures are the durable half of that requirement: the executable legacy code is
gone, the behaviour is recorded as data and marked pending human confirmation.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

ORACLE_DIR = Path(__file__).resolve().parents[1] / "benchmarks" / "oracle"
FIXTURES = sorted(ORACLE_DIR.glob("*-seed*.json"))

EXPECTED = {"arithmetic-seed7.json", "blackjack-seed7.json",
            "shedding-seed7.json", "high_card-seed7.json"}


def test_the_deleted_engines_left_behind_fixtures():
    assert EXPECTED <= {path.name for path in FIXTURES}


@pytest.mark.parametrize("path", FIXTURES, ids=[path.name for path in FIXTURES])
def test_fixture_records_a_complete_finished_game(path: Path):
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["confirmation"] == "pending_human"
    assert isinstance(data["known_defects"], list)
    assert data["rules"], "a fixture without rule samples cannot anchor a migration"
    assert data["events"]
    assert data["checks"]["finished"] is True
    assert data["checks"]["events"] == len(data["events"])
    assert any(event["event"] == "game_finished" for event in data["events"])


def test_shedding_fixture_proves_card_conservation():
    data = json.loads((ORACLE_DIR / "shedding-seed7.json").read_text(encoding="utf-8"))
    assert data["checks"]["card_conservation"] == 52


def test_no_legacy_engine_module_remains_in_the_package():
    """The deletion is real: the package must not ship the old engines."""
    package = Path(__file__).resolve().parents[1] / "src" / "pocker_agent"
    for legacy in ("engine.py", "family_engines.py", "game_rules.py", "executors.py",
                   "runtime.py", "simulation.py", "api.py", "agent.py"):
        assert not (package / legacy).exists(), f"{legacy} should have been deleted"
