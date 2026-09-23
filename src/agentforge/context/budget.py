"""Deterministic size limits for content returned to language models."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class BudgetedPayload:
    text: str
    original_characters: int
    truncated: bool


def budget_json_payload(payload: dict[str, Any], maximum_characters: int) -> BudgetedPayload:
    """Serialize a tool payload without exceeding its model-context allowance."""

    if maximum_characters < 200:
        raise ValueError("JSON payload budget must be at least 200 characters")
    original = json.dumps(payload, ensure_ascii=False, default=str)
    if len(original) <= maximum_characters:
        return BudgetedPayload(original, len(original), False)

    def render(preview: str) -> str:
        return json.dumps(
            {
                "ok": payload.get("ok", True),
                "truncated": True,
                "original_characters": len(original),
                "preview": preview,
            },
            ensure_ascii=False,
        )

    lower = 0
    upper = len(original)
    best = render("")
    while lower <= upper:
        midpoint = (lower + upper) // 2
        bounded = render(original[:midpoint])
        if len(bounded) <= maximum_characters:
            best = bounded
            lower = midpoint + 1
        else:
            upper = midpoint - 1
    return BudgetedPayload(best, len(original), True)
