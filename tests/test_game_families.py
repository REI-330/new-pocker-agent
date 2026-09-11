import copy
from fractions import Fraction

import pytest
from fastapi.testclient import TestClient

from pocker_agent.api import create_app
from pocker_agent.arithmetic import calculate, solve
from pocker_agent.engine import Card, build_deck
from pocker_agent.executors import create_engine, restore_engine
from pocker_agent.family_engines import hand_value
from pocker_agent.game_rules import parse_rule, rule_facts
from pocker_agent.runtime import RuntimeSession, RuntimeStore, snapshot


def rules_for(kind, **updates):
    rules = {"schema_version":"0.2", "kind":kind, "game_id":"test-"+kind, "title":"Test",
             "deck":{"suits":["S","H","D","C"],"ranks":["A",*map(str,range(2,11)),"J","Q","K"]},
             "players":{"min_players":2,"max_players":2,"starting_hand_size":2}, "max_rounds":3}
    if kind == "arithmetic":
        rules.update(target=24,card_count=4,rank_values={"A":1,**{str(n):n for n in range(2,11)},"J":11,"Q":12,"K":13},
                     operations=["+","-","*","/"],fractional_intermediates=True,deal_mode="random",dealing="fresh_deck_each_round",
                     players={"min_players":1,"max_players":1,"starting_hand_size":0})
    if kind == "blackjack":
        rules.update(target=21,dealer_stand_on=17,dealer_hits_soft_17=False,natural_beats_21=True,betting=False,dealing="fresh_deck_each_round")
    if kind == "shedding":
        rules.update(match="suit_or_rank",wild_rank="8",draw_policy="until_playable",recycle_discard=True,blocked_result="draw",max_rounds=1,
                     players={"min_players":2,"max_players":2,"starting_hand_size":5})
    rules.update(updates)
    return parse_rule(rules)


def c(rank, suit="S"):
    return Card(suit, str(rank), 0)


def test_arithmetic_exact_fractions_and_all_four_cards():
    assert calculate("8/(3-8/3)",[3,3,8,8],["+","-","*","/"]) == Fraction(24)
    assert calculate("（8÷（3-8÷3））",[3,3,8,8],["+","-","*","/"]) == 24
    assert solve((3,3,8,8),fractional=False) is None
    assert solve((1,1,1,1)) is None


@pytest.mark.parametrize("expression",["24", "8*3+3-3", "8**3", "__import__('os')", "8/(3-3)", "3+3+8+8+0", "-3+3+8+8", "3.0+3+8+8"])
def test_arithmetic_rejects_invalid_or_cheating_inputs(expression):
    with pytest.raises(ValueError): calculate(expression,[3,3,8,8],["+","-","*","/"])


@pytest.mark.parametrize("numbers",[(1,2,3,4),(3,3,8,8),(1,5,5,5),(4,4,10,10),(1,1,1,1)])
def test_solver_answers_are_independently_parsed(numbers):
    answer=solve(numbers)
    if answer is not None:
        assert calculate(answer,numbers,["+","-","*","/"]) == 24
    else:
        assert numbers == (1,1,1,1)


def test_wrong_answer_does_not_advance_or_change_score():
    engine=create_engine(rules_for("arithmetic"),7)
    engine.setup()
    before=copy.deepcopy(engine.serialize())
    with pytest.raises(ValueError): engine.step("submit_expression",expression="24")
    assert engine.serialize() == before
    engine.step("give_up")
    assert engine.state.players[0].score == 0 and engine.legal_actions() == ["next_round"]
    engine.step("next_round")
    assert engine.state.round_number == 2


def test_no_solution_claim_checked_and_solvable_filter():
    engine=create_engine(rules_for("arithmetic",deal_mode="solvable"),7)
    engine.setup()
    with pytest.raises(ValueError,match="有解"): engine.step("no_solution")
    engine.state.table=[c("A","S"),c("A","H"),c("A","D"),c("A","C")]
    engine.step("no_solution")
    assert engine.state.players[0].score == 1


@pytest.mark.parametrize("ranks,total,soft",[(["A","A","9"],21,True),(["A","6","10"],17,False),(["K","Q","2"],22,False),(["A","6"],17,True)])
def test_blackjack_card_values(ranks,total,soft):
    assert hand_value([c(r) for r in ranks]) == (total,soft)


def blackjack_state(human,dealer,**options):
    engine=create_engine(rules_for("blackjack",**options),7)
    engine.setup()
    engine.state.players[0].hand=[c(r,"H") for r in human]
    engine.state.players[1].hand=[c(r,"S") for r in dealer]
    for p in engine.state.players: p.score=0
    engine.state.finished=False
    engine.state.phase="要牌或停牌"
    engine.state.deck=[c(2,"D"),c(3,"D")]
    return engine


def test_blackjack_soft17_rule_changes_result():
    stay=blackjack_state(["10","8"],["A","6"])
    hit=blackjack_state(["10","8"],["A","6"],dealer_hits_soft_17=True)
    stay.step("stand"); hit.step("stand")
    assert stay.state.players[0].score == 1
    assert hit.state.players[1].score == 1


def test_blackjack_natural_precedes_three_card_21_and_player_bust_loses():
    engine=blackjack_state(["7","7","7"],["A","K"])
    engine.step("stand")
    assert engine.state.players[1].score == 1
    engine=blackjack_state(["K","Q","5"],["K","Q","5"])
    engine.settle()
    assert engine.state.players[1].score == 1


def test_blackjack_hole_card_and_total_are_private():
    engine=blackjack_state(["8","7"],["9","6"])
    view=snapshot(RuntimeSession("test",engine))
    assert len(view["players"][1]["hand"]) == 1
    assert view["players"][1]["hidden_count"] == 1
    assert view["players"][1]["total"] is None
    assert "seed" not in view
    engine.step("stand")
    assert len(snapshot(RuntimeSession("test",engine))["players"][1]["hand"]) >= 2


def test_shedding_checks_matching_and_wild_suit_before_mutating():
    engine=create_engine(rules_for("shedding"),7)
    engine.setup()
    engine.discard=[c(5,"H")]; engine.state.table=engine.discard[:]
    engine.extra["active_suit"]="H"
    engine.state.players[0].hand=[c(6,"S"),c(8,"D"),c(5,"C")]
    before=copy.deepcopy(engine.serialize())
    with pytest.raises(ValueError): engine.step("play",0)
    with pytest.raises(ValueError): engine.step("play",1)
    assert engine.serialize() == before
    engine.step("play",1,declared_suit="C")
    assert engine.extra["active_suit"] == "C"
    assert engine.state.current_player == 1


@pytest.mark.parametrize("kind",["arithmetic","blackjack","shedding"])
def test_seeded_play_is_reproducible_and_restorable(kind):
    rules=rules_for(kind)
    for seed in range(12):
        first=create_engine(rules,seed); second=create_engine(rules,seed)
        first.setup(); second.setup()
        assert first.serialize() == second.serialize()
        assert restore_engine(first.serialize()).serialize() == first.serialize()
        for _ in range(1000):
            if first.state.finished: break
            assert first.legal_actions()
            first.step(); second.step()
            assert first.serialize() == second.serialize()
            if kind == "shedding":
                cards=first.state.deck+first.discard+[c for p in first.state.players for c in p.hand]
                assert len(cards) == len(set(cards)) == 52
        assert first.state.finished


def test_api_arithmetic_submit_restart_and_revision(local_app):
    app,client,vault=local_app
    rules=rules_for("arithmetic",deal_mode="solvable",max_rounds=1)
    body=rules.model_dump(mode="json")
    confirm=client.post('/api/agent/confirm',json={"proposal":body})
    assert confirm.json()["kind"] == "confirmed"
    assert "24" in confirm.json()["facts"][0]
    state=client.post('/api/runtime/sessions?seed=7',json=body).json()
    sid=state["session_id"]
    path=f'/api/runtime/sessions/{sid}/actions/submit_expression'
    assert client.post(path,json={"revision":0,"expression":"24"}).status_code == 422
    assert client.get('/api/runtime/sessions/'+sid).json() == state
    answer=solve(tuple(state["numbers"]))
    final=client.post(path,json={"revision":0,"expression":answer}).json()["state"]
    assert final["finished"] and final["players"][0]["score"] == 1
    restarted=TestClient(create_app(app.state.config_store.path,vault))
    assert restarted.get('/api/runtime/sessions/'+sid).json() == final
    assert restarted.post(path,json={"revision":0,"expression":answer}).status_code == 409
    assert client.post('/api/games/export',json=body).status_code == 422


@pytest.mark.parametrize("kind",["arithmetic","blackjack","shedding"])
def test_api_new_families_simulate_with_same_engine(local_app,kind):
    _,client,_=local_app
    response=client.post('/api/simulations?seed=7',json=rules_for(kind).model_dump(mode="json"))
    assert response.status_code == 200
    assert response.json()["completed"]


def test_unsupported_rule_fields_cannot_be_ignored():
    body=rules_for("arithmetic").model_dump(mode="json")
    body['operations'].append('**')
    with pytest.raises(ValueError): parse_rule(body)
    body=rules_for("blackjack").model_dump(mode="json")
    body['betting']=True
    with pytest.raises(ValueError): parse_rule(body)


def exhausted_shedding(**options):
    engine=create_engine(rules_for("shedding",**options),7)
    engine.setup()
    engine.state.deck=[]
    engine.state.players[0].hand=[c(2,"S"),c(3,"S")]
    engine.state.players[1].hand=[c(4,"D")]
    top=c(5,"H")
    used={(card.rank,card.suit) for p in engine.state.players for card in p.hand} | {(top.rank,top.suit)}
    engine.discard=[card for card in build_deck(engine.rules) if (card.rank,card.suit) not in used]+[top]
    engine.state.table=[top]
    engine.extra['active_suit']='H'
    return engine


def test_recycle_keeps_top_card_and_restores_randomness():
    engine=exhausted_shedding()
    restored=restore_engine(engine.serialize())
    engine.step('draw');restored.step('draw')
    assert engine.serialize() == restored.serialize()
    assert engine.state.table == [c(5,'H')] and engine.discard == [c(5,'H')]
    assert len(engine.state.deck)==47 and len(engine.state.players[0].hand)==3
    assert any(e['event']=='discard_recycled' and e['count']==48 for e in engine.events)


@pytest.mark.parametrize('policy,winners',[('draw',['player-1','player-2']),('fewest_cards',['player-2'])])
def test_all_blocked_ends_according_to_explicit_rule(policy,winners):
    engine=exhausted_shedding(recycle_discard=False,blocked_result=policy)
    assert engine.legal_actions()==['pass']
    engine.step('pass')
    assert engine.state.finished and engine.state.winners==winners


@pytest.mark.parametrize("kind", [[], {}, None, "unknown"])
def test_invalid_family_is_a_validation_error(local_app, kind):
    _, client, _ = local_app
    body = rules_for("arithmetic").model_dump(mode="json")
    body["kind"] = kind
    response = client.post("/api/rules/validate", json=body)
    assert response.status_code == 200
    assert response.json()["valid"] is False and response.json()["errors"]


def test_shedding_rejects_hands_that_could_leave_only_wild_start_cards():
    with pytest.raises(ValueError, match="至少留5张"):
        rules_for("shedding", players={"min_players": 2, "max_players": 2, "starting_hand_size": 25})
    engine = create_engine(rules_for("shedding", wild_rank=None,
        players={"min_players": 2, "max_players": 2, "starting_hand_size": 25}), 7)
    engine.setup()
    assert len(engine.state.table) == 1 and len(engine.state.deck) == 1


def test_failed_bot_step_does_not_commit_partial_action_or_events(monkeypatch):
    store = RuntimeStore()
    session = store.create(rules_for("arithmetic"), 7)
    before = copy.deepcopy(snapshot(session))

    def fail_after_mutation(engine):
        engine.events[0]["kind"] = "corrupted"
        engine.emit("partial_bot_action")
        raise RuntimeError("bot_step_limit")

    monkeypatch.setattr("pocker_agent.runtime.run_bots", fail_after_mutation)
    with pytest.raises(RuntimeError, match="bot_step_limit"):
        store.act(session.id, "give_up", 0)
    assert snapshot(store.get(session.id)) == before
