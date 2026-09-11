> 历史文档（v0.2）：描述的是已删除的家族引擎架构，已被 `docs/development-design-v0.4.md` 取代。仅作追溯参考。

# Pocker Agent Demo Product Specification

## User outcome

A user can describe a new poker game in natural language, resolve ambiguities with an agent, inspect and confirm a structured rule definition, watch automated simulations, play one validated game in the browser, and export the result.

## Main flow

1. Enter a game idea.
2. Agent asks only the questions needed to make the rules executable.
3. Review confirmed rules, unresolved items, and the generated DSL.
4. Confirm the DSL.
5. Run simulations and inspect state/action/result events.
6. Enter single-player play against a simple legal-action strategy.
7. Download the DSL JSON and a self-contained Web game package.

## Acceptance criteria

- A complete flow works without login.
- The same DSL produces deterministic results when given the same seed.
- Invalid or unsupported rules stop before play and explain the reason.
- The UI exposes the agent's current state, rule changes, simulation progress, and errors.
- Exported DSL can be loaded by the runtime without the authoring UI.

## Explicit non-goals

Multiplayer networking, accounts, asset generation, moderation, rights management, matchmaking, chat, spectators, and production-scale hosting.
