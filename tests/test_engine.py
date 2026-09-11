from pocker_agent.engine import RuleEngine, build_deck
from pocker_agent.models import GameRuleDSL
from pocker_agent.simulation import simulate


def rules() -> GameRuleDSL:
    return GameRuleDSL.model_validate(
        {
            "game_id": "high-card",
            "title": "High Card",
            "description": "Draw one card and keep the highest card.",
            "deck": {"suits": ["S", "H"], "ranks": ["A", "K", "Q", "J"]},
            "players": {"min_players": 2, "max_players": 2, "starting_hand_size": 1},
            "actions": [{"name": "play", "source": "hand"}],
            "phases": [{"name": "main", "actions": ["play"], "max_turns": 2}],
            "max_rounds": 1,
        }
    )


def test_deck_contains_each_card_combination():
    assert len(build_deck(rules())) == 8


def test_simulation_completes_and_emits_trace():
    result = simulate(rules(), seed=7)
    assert result.completed is True
    assert result.winner in {"player-1", "player-2"}
    assert result.events[0]["event"] == "game_started"
    assert len([event for event in result.events if event["event"] == "card_drawn"]) == 2


def test_same_seed_is_deterministic():
    first = simulate(rules(), seed=42)
    second = simulate(rules(), seed=42)
    assert first.events == second.events


def test_illegal_action_is_rejected():
    engine = RuleEngine(rules(), seed=1)
    engine.setup()
    try:
        engine.step("draw")
    except ValueError as error:
        assert str(error) == "illegal_action:draw"
    else:
        raise AssertionError("illegal action was accepted")


def test_multi_round_game_starts_next_round_after_all_hands_are_played():
    multi_round = rules().model_copy(update={"max_rounds": 2})
    engine = RuleEngine(multi_round, seed=1)
    engine.setup()

    engine.step("play")
    engine.step("play")

    assert engine.state.finished is False
    assert engine.state.round_number == 2
    assert engine.state.table == []
    assert engine.legal_actions() == ["play"]
import pytest
from pocker_agent.engine import Card
from pocker_agent.exporting import compile_game
from pocker_agent.runtime import RuntimeSession, run_bots, snapshot


def test_scoring_uses_played_cards_and_handles_ties():
    engine = RuleEngine(rules())
    engine.setup()
    engine.state.players[0].hand = [Card("S", "A", 1)]
    engine.state.players[1].hand = [Card("S", "K", 9)]
    engine.step("play"); engine.step("play")
    assert engine.state.winners == ["player-2"]
    assert engine.state.players[1].score == 1


def test_phase_limits_and_pass_only_game_terminate():
    payload = rules().model_dump()
    payload["actions"] = [{"name":"pass","source":"system","amount":0}]
    payload["phases"] = [{"name":"first","actions":["pass"],"max_turns":2},{"name":"second","actions":["pass"],"max_turns":2}]
    engine = RuleEngine(GameRuleDSL.model_validate(payload))
    events = engine.run()
    assert len([e for e in events if e["event"] == "action_executed"]) == 4
    assert engine.state.finished


def test_no_legal_actions_is_terminal_even_at_setup():
    payload = rules().model_dump()
    payload["players"]["starting_hand_size"] = 0
    engine = RuleEngine(GameRuleDSL.model_validate(payload))
    engine.setup()
    assert engine.state.finished and not engine.legal_actions()
    assert engine.state.finish_reason == "no_legal_actions"


def test_simulation_limit_is_reported_not_fabricated_success():
    with pytest.raises(RuntimeError,match="simulation_step_limit"):
        simulate(rules(), max_steps=1)


def test_offline_compilation_matches_canonical_engine():
    nodes = compile_game(rules(), seed=7)
    engine = RuleEngine(rules(),seed=7); engine.setup()
    edge = nodes[0]["moves"][0]
    engine.step(edge["action"],edge["card_index"]); run_bots(engine)
    compiled = nodes[edge["next"]]["state"]
    live = snapshot(RuntimeSession("offline",engine,seed=7))
    for field in ("players","table","finished","round","winners"):
        assert compiled[field] == live[field]

def test_tie_awards_both_players_instead_of_arbitrary_winner():
    engine = RuleEngine(rules()); engine.setup()
    engine.state.players[0].hand = [Card("S", "K", 4)]
    engine.state.players[1].hand = [Card("H", "K", 4)]
    engine.step("play"); engine.step("play")
    assert engine.state.winners == ["player-1", "player-2"]
    assert [p.score for p in engine.state.players] == [1, 1]


def test_draw_with_empty_hand_advances_to_play_phase():
    payload = rules().model_dump()
    payload["players"]["starting_hand_size"] = 0
    payload["actions"] = [{"name":"draw","source":"system","amount":2},{"name":"play","source":"hand","amount":2}]
    payload["phases"] = [{"name":"draw","actions":["draw"],"max_turns":2},{"name":"play","actions":["play"],"max_turns":2}]
    engine = RuleEngine(GameRuleDSL.model_validate(payload)); engine.setup()
    engine.step("draw"); engine.step("draw")
    assert engine.state.phase == "play"
    assert [len(p.hand) for p in engine.state.players] == [2,2]
    engine.step("play"); engine.step("play")
    assert len(engine.state.table) == 4 and engine.state.finished


def test_exhausted_deck_ends_explicitly_before_next_round():
    payload = rules().model_dump()
    payload["deck"] = {"suits":["S"],"ranks":["2","3"]}
    payload["max_rounds"] = 3
    engine = RuleEngine(GameRuleDSL.model_validate(payload))
    engine.run()
    assert engine.state.round_number == 1
    assert engine.state.finish_reason == "deck_exhausted"


def test_discard_does_not_count_as_played_card():
    payload = rules().model_dump()
    payload["actions"] = [{"name":"discard"}]
    payload["phases"] = [{"name":"main","actions":["discard"],"max_turns":2}]
    engine = RuleEngine(GameRuleDSL.model_validate(payload)); engine.setup()
    engine.step("discard")
    assert not engine.state.table and not engine.state.players[0].played
