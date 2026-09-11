from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from .game_rules import PlayableRule, parse_rule


def validate_dsl(payload: dict[str, Any]) -> tuple[PlayableRule | None, list[str]]:
    try:
        return parse_rule(payload), []
    except ValidationError as error:
        return None, [f"{'.'.join(str(item) for item in issue['loc'])}: {issue['msg']}" for issue in error.errors()]
    except ValueError as error:
        return None, [str(error)]
