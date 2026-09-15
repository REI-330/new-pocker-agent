"""Betting, chip-ledger and poker-hand mechanisms.

These are orthogonal to the card-movement tools: ``betting`` runs a no-limit
betting round, ``ledger`` owns pots and settlement, and ``poker_hand`` ranks
five-card hands. All three are configuration-driven and hold no private state.
"""
from __future__ import annotations

from collections import Counter
from itertools import combinations
from typing import Any

from .cards import CardRef
from .contracts import ToolError

CATEGORY_NAMES = ("high_card", "pair", "two_pair", "three_of_a_kind", "straight",
                  "flush", "full_house", "four_of_a_kind", "straight_flush")


def _score_five(cards: list[CardRef]) -> tuple[int, ...]:
    values = sorted((card.value for card in cards), reverse=True)
    counts = Counter(values)
    flush = len({card.suit for card in cards}) == 1
    distinct = sorted(set(values), reverse=True)
    straight_high = None
    if len(distinct) == 5:
        if distinct[0] - distinct[4] == 4:
            straight_high = distinct[0]
        elif distinct == [14, 5, 4, 3, 2]:      # the wheel
            straight_high = 5
    by_count = sorted(counts.items(), key=lambda item: (-item[1], -item[0]))
    if straight_high and flush:
        return (8, straight_high)
    if by_count[0][1] == 4:
        return (7, by_count[0][0], by_count[1][0])
    if by_count[0][1] == 3 and by_count[1][1] == 2:
        return (6, by_count[0][0], by_count[1][0])
    if flush:
        return (5, *values)
    if straight_high:
        return (4, straight_high)
    if by_count[0][1] == 3:
        kickers = [value for value in values if value != by_count[0][0]]
        return (3, by_count[0][0], *kickers)
    if by_count[0][1] == 2 and by_count[1][1] == 2:
        pairs = sorted((by_count[0][0], by_count[1][0]), reverse=True)
        kickers = [value for value in values if value not in pairs]
        return (2, *pairs, *kickers)
    if by_count[0][1] == 2:
        kickers = [value for value in values if value != by_count[0][0]]
        return (1, by_count[0][0], *kickers)
    return (0, *values)


class HandRankTool:
    """Best five-card hand out of the supplied cards (poker categories)."""

    def best(self, cards: Any) -> dict[str, Any]:
        if not isinstance(cards, list) or len(cards) < 5:
            raise ToolError("poker_hand_requires_at_least_five_cards")
        if not all(isinstance(card, CardRef) for card in cards):
            raise ToolError("poker_hand_requires_cards")
        if len({card.id for card in cards}) != len(cards):
            raise ToolError("poker_hand_requires_unique_cards")
        winning = max(combinations(cards, 5), key=_score_five)
        score = _score_five(list(winning))
        return {"category": CATEGORY_NAMES[score[0]], "rank": score[0],
                "score": list(score), "cards": [card.id for card in winning]}

    def compare(self, left: Any, right: Any) -> dict[str, Any]:
        best_left, best_right = self.best(left), self.best(right)
        outcome = "tie"
        if best_left["score"] > best_right["score"]:
            outcome = "left"
        elif best_right["score"] > best_left["score"]:
            outcome = "right"
        return {"outcome": outcome, "left": best_left["category"],
                "right": best_right["category"]}


class LedgerTool:
    """Chip ledger: commits, main/side pots (with refunds) and settlement."""

    def commit(self, state: dict[str, Any], seat: int, amount: int) -> dict[str, Any]:
        stacks = state.get("stacks")
        if not isinstance(stacks, list) or not 0 <= seat < len(stacks):
            raise ToolError("invalid_seat")
        committed = state.get("committed")
        hand_committed = state.get("hand_committed")
        if (not isinstance(committed, list) or len(committed) != len(stacks)
                or not isinstance(hand_committed, list) or len(hand_committed) != len(stacks)):
            raise ToolError("ledger_size_mismatch")
        values = [*stacks, *committed, *hand_committed]
        if any(type(value) is not int or value < 0 for value in values):
            raise ToolError("ledger_values_must_be_non_negative_integers")
        if type(amount) is not int or amount < 0 or amount > stacks[seat]:
            raise ToolError("invalid_commit")
        stacks[seat] -= amount
        committed[seat] += amount
        hand_committed[seat] += amount
        return {"seat": seat, "amount": amount, "stacks": list(stacks)}

    def pots(self, state: dict[str, Any]) -> list[dict[str, Any]]:
        committed = state.get("hand_committed") or []
        players = len(state.get("stacks") or [])
        if len(committed) != players or players == 0:
            raise ToolError("ledger_size_mismatch")
        if any(type(value) is not int or value < 0 for value in committed):
            raise ToolError("ledger_values_must_be_non_negative_integers")
        folded = set(state.get("folded") or [])
        if not folded <= set(range(players)):
            raise ToolError("invalid_folded_seat")
        pots: list[dict[str, Any]] = []
        previous = 0
        for level in sorted({value for value in committed if value > 0}):
            contributors = [seat for seat, value in enumerate(committed) if value >= level]
            pots.append({"amount": (level - previous) * len(contributors),
                         "eligible": [seat for seat in contributors if seat not in folded],
                         "refund_to": contributors[0] if len(contributors) == 1 else None})
            previous = level
        return pots

    def total(self, state: dict[str, Any]) -> int:
        return sum(pot["amount"] for pot in self.pots(state))

    def settle(self, state: dict[str, Any], winners: dict) -> dict[str, Any]:
        if not isinstance(winners, dict):
            raise ToolError("pot_winners_must_be_object")
        pots = self.pots(state)
        awards = [0] * len(state["stacks"])
        contested = [index for index, pot in enumerate(pots) if pot["refund_to"] is None]
        missing = [index for index in contested if not (winners.get(index) or winners.get(str(index)))]
        if missing:
            raise ToolError("every_contested_pot_requires_winners")
        for index, pot in enumerate(pots):
            if pot["refund_to"] is not None:
                awards[pot["refund_to"]] += pot["amount"]
                continue
            chosen = sorted(winners.get(index) or winners.get(str(index)))
            if not chosen:
                raise ToolError("contested_pot_requires_winner")
            if len(chosen) != len(set(chosen)):
                raise ToolError("duplicate_pot_winner")
            if not set(chosen) <= set(pot["eligible"]):
                raise ToolError("ineligible_pot_winner")
            share, odd = divmod(pot["amount"], len(chosen))
            for position, seat in enumerate(chosen):
                awards[seat] += share + (1 if position < odd else 0)
        state["stacks"] = [stack + award for stack, award in zip(state["stacks"], awards)]
        state["committed"] = [0] * len(state["stacks"])
        state["hand_committed"] = [0] * len(state["stacks"])
        return {"awards": awards, "pots": pots, "stacks": list(state["stacks"])}


class BettingTool:
    """One no-limit betting round over the shared state.

    Streets are the plan's business; this tool only knows how to offer the
    legal actions, apply them, and report when the round is complete.
    """

    def __init__(self, min_raise: int = 1) -> None:
        if type(min_raise) is not int or min_raise <= 0:
            raise ToolError("invalid_min_raise")
        self.min_raise = min_raise

    @staticmethod
    def _folded(state: dict[str, Any]) -> set[int]:
        return set(state.get("folded") or [])

    @staticmethod
    def _validate_state(state: dict[str, Any]) -> None:
        stacks = state.get("stacks")
        committed = state.get("committed")
        hand_committed = state.get("hand_committed")
        if (not isinstance(stacks, list) or not stacks
                or not isinstance(committed, list) or len(committed) != len(stacks)
                or not isinstance(hand_committed, list) or len(hand_committed) != len(stacks)):
            raise ToolError("betting_ledger_size_mismatch")
        if any(type(value) is not int or value < 0
               for value in [*stacks, *committed, *hand_committed]):
            raise ToolError("betting_ledger_values_invalid")
        players = set(range(len(stacks)))
        for name in ("folded", "acted"):
            seats = state.get(name) or []
            if (not isinstance(seats, list) or len(seats) != len(set(seats))
                    or any(type(seat) is not int for seat in seats)
                    or not set(seats) <= players):
                raise ToolError(f"betting_{name}_invalid")
        seat = state.get("current_player", 0)
        if type(seat) is not int or seat not in players:
            raise ToolError("invalid_current_player")
        for name in ("current_bet", "min_raise"):
            value = state.get(name, 0)
            if type(value) is not int or value < 0:
                raise ToolError(f"betting_{name}_invalid")

    def _to_call(self, state: dict[str, Any]) -> int:
        seat = state["current_player"]
        return max(0, int(state.get("current_bet", 0)) - state["committed"][seat])

    def legal(self, state: dict[str, Any]) -> list[str]:
        self._validate_state(state)
        stacks = state.get("stacks") or []
        seat = state.get("current_player", 0)
        if not 0 <= seat < len(stacks) or seat in self._folded(state) or stacks[seat] <= 0:
            return []
        to_call = self._to_call(state)
        actions = ["fold", "check" if to_call == 0 else "call"]
        acted = set(state.get("acted") or [])
        max_target = state["committed"][seat] + stacks[seat]
        min_target = int(state.get("current_bet", 0)) + int(
            state.get("min_raise", self.min_raise))
        if seat not in acted and max_target >= min_target:
            actions.append("raise")
        # A player whose action was not reopened may still call all-in, but may
        # not use all-in to make a further raise.
        if stacks[seat] <= to_call or seat not in acted:
            actions.append("all_in")
        return actions

    def _live(self, state: dict[str, Any]) -> list[int]:
        folded = self._folded(state)
        return [seat for seat in range(len(state["stacks"])) if seat not in folded]

    def _complete(self, state: dict[str, Any]) -> bool:
        live = self._live(state)
        if len(live) <= 1:
            return True
        acted = set(state.get("acted") or [])
        bet = int(state.get("current_bet", 0))
        for seat in live:
            if state["stacks"][seat] == 0:          # all-in: nothing left to match
                continue
            if seat not in acted or state["committed"][seat] != bet:
                return False
        return True

    def _next_seat(self, state: dict[str, Any]) -> int:
        live = self._live(state)
        seat = state["current_player"]
        for step in range(1, len(state["stacks"]) + 1):
            candidate = (seat + step) % len(state["stacks"])
            if candidate in live and state["stacks"][candidate] > 0:
                return candidate
        return live[0]

    def act(self, state: dict[str, Any], action: str, amount: int = 0) -> dict[str, Any]:
        actions = self.legal(state)
        if action not in actions:
            raise ToolError(f"illegal_bet_action:{action}")
        seat = state["current_player"]
        stacks, committed = state["stacks"], state["committed"]
        to_call = self._to_call(state)
        bet = int(state.get("current_bet", 0))
        if action == "fold":
            folded = state.setdefault("folded", [])
            if seat not in folded:
                folded.append(seat)
        elif action == "check":
            pass
        elif action == "call":
            LedgerTool().commit(state, seat, min(to_call, stacks[seat]))
        elif action == "raise":
            target = int(amount)
            if target < bet + int(state.get("min_raise", self.min_raise)) or target > committed[seat] + stacks[seat]:
                raise ToolError("invalid_raise")
            LedgerTool().commit(state, seat, target - committed[seat])
            state["min_raise"] = target - bet
            state["current_bet"] = target
            state["acted"] = []
        else:  # all_in
            amount = stacks[seat]
            LedgerTool().commit(state, seat, amount)
            if committed[seat] > bet:
                raise_size = committed[seat] - bet
                if raise_size >= int(state.get("min_raise", self.min_raise)):
                    state["min_raise"] = raise_size
                    state["acted"] = []
                state["current_bet"] = committed[seat]
        acted = state.setdefault("acted", [])
        if seat not in acted:
            acted.append(seat)
        state["street_done"] = self._complete(state)
        if not state["street_done"]:
            state["current_player"] = self._next_seat(state)
        return {"action": action, "seat": seat, "to_call": to_call,
                "committed": list(committed), "street_done": state["street_done"],
                "folded": list(self._folded(state))}
