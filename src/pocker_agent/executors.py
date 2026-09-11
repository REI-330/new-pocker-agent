"""Single dispatch boundary shared by HTTP runtime and deterministic simulation."""
from copy import deepcopy

from .engine import RuleEngine
from .family_engines import ArithmeticEngine, BlackjackEngine, SheddingEngine, restore_state
from .game_rules import parse_rule

FAMILIES = {"arithmetic": ArithmeticEngine, "blackjack": BlackjackEngine, "shedding": SheddingEngine}


def create_engine(rules, seed=0, player_count=None):
    return FAMILIES.get(getattr(rules, "kind", None), RuleEngine)(rules, seed=seed, player_count=player_count)


def restore_engine(data):
    # Runtime uses restoration as a transaction copy. Events and nested state
    # must not alias the source when a later action or bot step is rejected.
    data = deepcopy(data)
    rules = parse_rule(data["rules"])
    if not hasattr(rules, "kind"): return RuleEngine.restore(data)
    engine = create_engine(rules, seed=data["seed"])
    engine.state = restore_state(data["state"])
    engine.events, engine.extra = data["events"], dict(data["extra"])
    from .engine import Card
    engine.discard = [Card(**c) for c in data["discard"]]
    return engine
