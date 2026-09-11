# Contributing

Work is organized around vertical slices. Keep the DSL, rule engine, API, and UI contracts versioned together.

Before submitting a change:

1. Run the focused tests for the affected module.
2. Run the complete demo flow when changing persistence, API contracts, or runtime behavior.
3. Document unsupported rule constructs instead of silently accepting them.

Use small commits with an imperative subject, for example `Define DSL v0.1 schema`.
