# Third-party software and assets

## Reused in the application

- OpenAI Python SDK: https://github.com/openai/openai-python — Apache-2.0. Replaces handwritten urllib transport/authentication; arbitrary compatible base_url and model remain supported. Version is pinned in uv.lock.
- keyring: https://github.com/jaraco/keyring — MIT. Uses the Windows credential vault for API keys; metadata and game state remain in Python standard-library SQLite.
- React / Vite / FastAPI / Pydantic: existing framework dependencies, resolved versions retained in lockfiles.
- Playing Cards Assets: https://github.com/hayeah/playing-cards-assets — MIT, copyright Howard Yeh (2018). Source README credits the original vector-playing-cards artwork as public domain.
  - Vendored revision: 1e4497c05c3da9956c9f517bd386e9a7090ff7fa.
  - Files: src/pocker_agent/assets/cards/*.svg (52 standard cards and 2 jokers).
  - Original license: src/pocker_agent/assets/cards/LICENSE. Included in every offline game ZIP.
  - SVG artwork is used as supplied; no AI image generation.

## Evaluated, not integrated

- boardgame.io: https://github.com/boardgameio/boardgame.io — MIT. Strong choice for a future TypeScript multiplayer server. Current demo has a Python DSL executor and no multiplayer; adding a second state authority would increase inconsistency. Re-evaluate for multiplayer rather than porting part of its protocol.
- deck-of-cards: https://github.com/deck-of-cards/deck-of-cards — older animation-oriented library; GitHub license detection was NOASSERTION during review. Chose clearly licensed SVG assets instead.
