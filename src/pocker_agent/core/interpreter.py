"""Deterministic interpreter for a ``GamePlan``.

Responsibilities are deliberately narrow:
- resolve ``$state.a.b`` references (never evaluate code),
- dispatch only declared operations of declared tools,
- keep every rejection transactional (state + events + pc roll back),
- expose ``legal_actions`` for the ``wait`` node, nothing else.

There is no model, no network and no clock in this file, so a session is fully
reproducible from ``serialize()``.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from .actions import resolve_zone
from .cards import decode, encode
from .contracts import ToolError, ToolRegistry
from .plan import GamePlan
from .tools import evaluate_expression


class Interpreter:
    execution_mode = "core"

    def __init__(self, plan: GamePlan, registry: ToolRegistry, seed: int = 0) -> None:
        self.plan = plan
        self.registry = registry
        self.seed = seed
        self.tools: dict[str, Any] = {
            binding.name: registry.create(binding.name, **binding.config)
            for binding in plan.tools
        }
        # Fail fast: a plan naming an operation the host never declared is a
        # contract violation, not a runtime surprise halfway through a game.
        for node in plan.nodes.values():
            if node.action is not None:
                registry.spec(node.action.tool).operation(node.action.operation)
        self.state: dict[str, Any] = deepcopy(plan.initial)
        self.state["seed"] = seed
        self.state["input"] = {}
        self.events: list[dict[str, Any]] = []
        self.pc = plan.entry
        self.started = False

    # ---------------------------------------------------------------- events
    def emit(self, event: str, **payload: Any) -> dict[str, Any]:
        entry = {"event": event, "round": self.state.get("round", 1), **payload}
        self.events.append(entry)
        return entry

    # ------------------------------------------------------------ references
    def resolve(self, value: Any) -> Any:
        """Resolve ``$state.x.y`` only; other strings stay literal."""
        if isinstance(value, str) and (value == "$state" or value.startswith("$state.")):
            path = value[len("$state"):].lstrip(".")
            if not path:
                return self.state
            current: Any = self.state
            for part in path.split("."):
                if isinstance(current, dict) and part in current:
                    current = current[part]
                elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
                    current = current[int(part)]
                else:
                    raise ToolError(f"state_reference_not_found:{value}")
            return current
        if isinstance(value, dict):
            return {key: self.resolve(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self.resolve(item) for item in value]
        return value

    # -------------------------------------------------------------- dispatch
    def _check_predicates(self, predicates, kind: str, action) -> None:
        """Evaluate a tool contract predicate against the current state.

        A false predicate is a contract violation, not a player error, so it is
        reported with the operation name and rolled back by the caller.
        """
        for predicate in predicates:
            if not evaluate_expression(self.resolve(predicate)):
                raise ToolError(f"{kind}_failed:{action.tool}.{action.operation}")

    def call(self, action) -> Any:
        spec = self.registry.spec(action.tool)
        operation = spec.operation(action.operation)
        tool = self.tools[action.tool]
        method = getattr(tool, operation.method, None)
        if not callable(method):
            raise ToolError(f"unknown_tool_operation:{action.tool}.{action.operation}")
        args = self.resolve(action.args)
        if not isinstance(args, dict):
            raise ToolError("tool_args_must_be_object")
        self._check_predicates(operation.requires, "precondition", action)
        before = deepcopy(self.state)
        try:
            result = method(**args)
        except ToolError:
            raise
        except TypeError as error:
            raise ToolError(f"invalid_tool_args:{action.tool}.{action.operation}") from error
        # Tools index shared state directly, so a plan that never dealt part of
        # the state it needs (a poker plan without `current_player`) raises a raw
        # lookup error here. That is a plan bug and must arrive as a rejectable
        # contract violation with a rollback, not as a crash that escapes the
        # playtest gate and surfaces as an HTTP 500.
        except (KeyError, IndexError, AttributeError) as error:
            raise ToolError(
                f"tool_state_missing:{action.tool}.{action.operation}:{error!r}") from error
        if action.result_key:
            self.state[action.result_key] = result
        # Contract enforcement: an operation may only change the state keys it
        # declared (plus its own result_key). "*" opts out (only state.update).
        if "*" not in operation.effects:
            allowed = set(operation.effects)
            if action.result_key:
                allowed.add(action.result_key)
            changed = {key for key in set(before) | set(self.state)
                       if before.get(key) != self.state.get(key)}
            offending = sorted(changed - allowed)
            if offending:
                raise ToolError("out_of_contract_state_change:" + ",".join(offending))
        self._check_predicates(operation.ensures, "postcondition", action)
        self.emit("tool_called", tool=action.tool, operation=action.operation,
                  result_key=action.result_key)
        return result

    # ----------------------------------------------------------------- flow
    def advance(self) -> None:
        for _ in range(self.plan.step_limit):
            node = self.plan.nodes[self.pc]
            if node.kind == "call":
                self.call(node.action)
                if self.events and self.events[-1].get("event") == "tool_called":
                    self.events[-1]["flow_node"] = self.pc
                self.pc = node.next
            elif node.kind == "branch":
                value = self.resolve(node.value)
                target = next((case.target for case in node.cases
                               if type(value) is type(case.value) and value == case.value), None)
                self.pc = target or node.next
            elif node.kind == "wait":
                if self.state.get("finished"):
                    raise ToolError("finished_flow_cannot_wait")
                self.emit("flow_wait", flow_node=self.pc)
                return
            else:  # end
                if not self.state.get("finished"):
                    raise ToolError("flow_end_requires_finished_state")
                self.emit("game_finished", winners=list(self.state.get("winners", [])),
                          scores=list(self.state.get("scores", [])))
                return
        raise ToolError("flow_step_limit")

    def setup(self) -> list[dict[str, Any]]:
        if self.started:
            raise ToolError("game_already_started")
        backup = (deepcopy(self.state), deepcopy(self.events), self.pc)
        try:
            self.emit("game_started", kind=self.plan.game_kind, execution_mode=self.execution_mode)
            self.advance()
        except Exception:
            self.state, self.events, self.pc = backup
            raise
        self.started = True
        return self.events

    def legal_actions(self) -> list[str]:
        node = self.plan.nodes[self.pc]
        if not self.started or node.kind != "wait" or self.state.get("finished"):
            return []
        return self._available(list(node.inputs))

    def _available(self, patterns: list[str]) -> list[str]:
        """Expand a wait node's static inputs against ``state.available_actions``.

        A plan may publish concrete options (e.g. ``ask:7``) for a wildcard input
        (``ask:*``); the engine filters and expands, it never invents an action
        that the plan did not offer.
        """
        available = self.state.get("available_actions")
        if not isinstance(available, list):
            return patterns
        resolved: list[str] = []
        for pattern in patterns:
            if pattern in available:
                resolved.append(pattern)
            elif pattern.endswith("*"):
                prefix = pattern[:-1]
                resolved.extend(action for action in available
                                if isinstance(action, str) and action.startswith(prefix))
        return resolved

    def step(self, action: str | None = None, card_index: int = 0, **payload: Any) -> dict[str, Any]:
        actions = self.legal_actions()
        if action is None:
            action = actions[0] if actions else None
        node_inputs = self.plan.nodes[self.pc].inputs
        key = action if action in node_inputs else next(
            (pattern for pattern in node_inputs if pattern.endswith("*")
             and isinstance(action, str) and action.startswith(pattern[:-1])), None)
        if key is None or (action not in actions and key not in actions):
            raise ToolError("illegal_action")
        backup = (deepcopy(self.state), deepcopy(self.events), self.pc)
        try:
            self.state["input"] = {"action": action, "card_index": card_index,
                                     "expression": "", "declared_suit": "", "amount": 0,
                                     **payload}
            self.pc = node_inputs[key]
            self.advance()
        except Exception:
            self.state, self.events, self.pc = backup
            raise
        return self.events[-1]

    def run(self, max_steps: int = 1024) -> list[dict[str, Any]]:
        self.setup()
        for _ in range(max_steps):
            if self.state.get("finished"):
                return self.events
            self.step()
        raise ToolError("simulation_step_limit")

    # ------------------------------------------------------------ persistence
    def serialize(self) -> dict[str, Any]:
        return {"executor": self.execution_mode, "plan": self.plan.model_dump(mode="json"),
                "seed": self.seed, "pc": self.pc, "started": self.started,
                "state": encode(self.state), "events": deepcopy(self.events)}

    @classmethod
    def restore(cls, data: dict[str, Any], registry: ToolRegistry) -> Interpreter:
        plan = GamePlan.model_validate(data["plan"])
        interpreter = cls(plan, registry, seed=data["seed"])
        interpreter.pc, interpreter.started = data["pc"], data["started"]
        if interpreter.pc not in plan.nodes:
            raise ToolError("saved_node_missing")
        interpreter.state = decode(data["state"])
        interpreter.events = deepcopy(data["events"])
        return interpreter

    # ------------------------------------------------------------------ view
    @staticmethod
    def _viewer_index(viewer: str) -> int | None:
        if isinstance(viewer, str) and viewer.startswith("player-"):
            suffix = viewer.split("-", 1)[1]
            if suffix.isdigit():
                return int(suffix) - 1
        return None

    def project_zones(self, viewer: str = "player-1") -> dict[str, Any]:
        """A viewer-aware projection of ``state['zones']``.

        ``public`` zones show their cards to everyone; ``owner_only`` zones only
        to the owning seat (or once the game is finished/revealed); ``hidden``
        zones never expose identities, only a count. This is a *view* rule: it
        stops the projection from leaking hidden cards without pretending the
        host already enforces per-viewer action permissions.
        """
        zones = self.state.get("zones")
        if not isinstance(zones, dict):
            return {}
        index = self._viewer_index(viewer)
        reveal = bool(self.state.get("reveal")) or bool(self.state.get("finished"))
        projected: dict[str, Any] = {}
        for zone_id, entry in sorted(zones.items()):
            cards = entry.get("cards") or []
            visibility = entry.get("visibility", "public")
            owner = entry.get("owner")
            visible = (reveal or visibility == "public"
                       or (visibility == "owner_only" and owner is not None and owner == index))
            projected[zone_id] = {
                "owner": owner, "visibility": visibility, "count": len(cards),
                "cards": [card.as_dict() for card in cards] if visible else [],
                "visible": visible}
        return projected

    def action_descriptors(self, viewer: str = "player-1") -> list[dict[str, Any]]:
        """The plan's wait actions with viewer-filtered candidate values.

        Each declared input is annotated with ``options`` -- the card ids visible
        to ``viewer`` in the zone the input reads. A hidden or other-owned zone
        yields an empty list, so the same call is safe to serve to any viewer;
        the descriptors themselves carry no card identities.
        """
        if not self.plan.actions:
            return []
        projected = self.project_zones(viewer)
        result: list[dict[str, Any]] = []
        for descriptor in self.plan.actions:
            inputs: list[dict[str, Any]] = []
            for item in descriptor.inputs:
                try:
                    zone_id = resolve_zone(self.state, item)
                except ToolError:
                    zone_id = None
                zone = projected.get(zone_id) if zone_id else None
                inputs.append({
                    "id": item.id, "kind": item.kind, "zone": item.zone,
                    "scope": item.scope, "min_count": item.min_count,
                    "max_count": item.max_count,
                    "options": [card["id"] for card in zone["cards"]] if zone else []})
            result.append({"id": descriptor.id, "label": descriptor.label,
                           "inputs": inputs})
        return result

    def view(self, viewer: str = "player-1") -> dict[str, Any]:
        state = self.state
        scores = list(state.get("scores", []))
        hands = state.get("hands")
        private = (bool(state.get("private_hands")) and not state.get("reveal")
                   and not state.get("finished"))
        known_hands = isinstance(hands, list) and bool(hands)
        count = len(hands) if known_hands else len(scores)
        players = []
        for index in range(count):
            hand = hands[index] if known_hands and index < len(hands) else []
            player_id = f"player-{index + 1}"
            visible = not (private and player_id != viewer)
            players.append({"id": player_id,
                            "hand": [card.as_dict() for card in hand] if visible else [],
                            "score": scores[index] if index < len(scores) else 0,
                            "hidden_count": 0 if visible else len(hand)})
        result = {
            "kind": self.plan.game_kind, "execution_mode": self.execution_mode,
            "flow_node": self.pc, "round": state.get("round", 1),
            "max_rounds": state.get("max_rounds", 1), "phase": state.get("phase", self.pc),
            "current_player": f"player-{int(state.get('current_player', 0)) + 1}",
            "finished": bool(state.get("finished")), "winners": [
                f"player-{int(i) + 1}" for i in state.get("winners", [])],
            "legal_actions": self.legal_actions(),
            "players": players,
            "scores": scores,
            "table": [card.as_dict() for card in state.get("table", [])],
            "numbers": list(state.get("numbers", [])),
            "target": state.get("target"),
            "reveal": bool(state.get("reveal", False)),
            "solution": state.get("solution"),
            "instructions": state.get("instructions", ""),
            "feedback": state.get("feedback", ""),
            "legal_card_indices": list(state.get("legal_card_indices", [])),
            "wild_ranks": list(state.get("wild_ranks", [])),
            "private_hands": private,
            "events": self.events[-100:],
        }
        projected = self.project_zones(viewer)
        if projected:
            result["zones"] = projected
        actions = self.action_descriptors(viewer)
        if actions:
            result["actions"] = actions
        # Extra table state (chips, teams, tricks, pairs) is exposed read-only so
        # a UI can render it without knowing the game family.
        for key in ("pot", "stacks", "committed", "hand_committed", "folded",
                    "teams", "tricks_won", "trump", "current_bet", "min_raise", "pairs"):
            if key in state:
                result[key] = state[key]
        return result
