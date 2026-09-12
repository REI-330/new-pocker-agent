"""Static control-flow analysis for a compiled ``GamePlan``.

M2 must check the *shape* of what it emits (plan section 6 / M2.4): every node is
reachable from the entry, and every cycle is explicitly bounded by a counter that
the compiler increments and tests. It must **not** claim a general termination
proof -- arbitrary graphs are only reported as inconclusive.

The compiler lists the nodes it knows to be bounded counters (``set_round`` /
``set_turn_index``); a cycle that avoids all of them is reported. A caller with no
such knowledge passes an empty set and gets "no bounded counter found" rather
than a false "terminates".
"""
from __future__ import annotations

from typing import Any

from ..plan import GamePlan


def successors(node: Any) -> list[str]:
    out: list[str] = []
    if node.next is not None:
        out.append(node.next)
    out.extend(case.target for case in node.cases)
    out.extend(value for value in node.inputs.values())
    return out


def reachable_nodes(plan: GamePlan) -> set[str]:
    seen: set[str] = set()
    stack = [plan.entry]
    while stack:
        node_id = stack.pop()
        if node_id in seen:
            continue
        seen.add(node_id)
        for target in successors(plan.nodes[node_id]):
            if target not in seen:
                stack.append(target)
    return seen


def analyse_control_flow(plan: GamePlan,
                         bounded_nodes: frozenset[str] = frozenset()) -> dict[str, Any]:
    """Reachability and bounded-cycle report for a plan.

    ``inconclusive`` is set when a cycle is found but the caller supplied no
    bounded-counter knowledge: the host then knows it cannot prove termination,
    instead of pretending it did.
    """
    reached = reachable_nodes(plan)
    unreachable = sorted(set(plan.nodes) - reached)

    unbounded: set[tuple[str, ...]] = set()
    visiting: set[str] = set()
    finished: set[str] = set()
    path: list[str] = []

    def walk(node_id: str) -> None:
        visiting.add(node_id)
        path.append(node_id)
        for target in successors(plan.nodes[node_id]):
            if target in visiting:
                cycle = tuple(sorted(path[path.index(target):]))
                if not (set(cycle) & bounded_nodes):
                    unbounded.add(cycle)
            elif target not in finished:
                walk(target)
        path.pop()
        visiting.discard(node_id)
        finished.add(node_id)

    for node_id in plan.nodes:
        if node_id not in finished:
            walk(node_id)

    return {"reachable": sorted(reached), "unreachable": unreachable,
            "bounded_nodes": sorted(bounded_nodes),
            "unbounded_cycles": [list(cycle) for cycle in sorted(unbounded)],
            "inconclusive": bool(unbounded) and not bounded_nodes}
