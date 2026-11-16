"""Declarative settings for a finder: description for the skill, validation for the config file."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Sequence

from cloudfix.common.errors import InputError

SettingType = Literal[
    "string", "integer", "number", "string_list", "account_id", "region", "choice", "path"
]

_REGION = re.compile(r"[a-z]{2}(-[a-z]+)+-[0-9]")
_ACCOUNT = re.compile(r"[0-9]{12}")


@dataclass(frozen=True)
class Setting:
    name: str
    question: str
    type: SettingType
    default: Any = None
    required: bool = False
    choices: tuple[str, ...] = ()
    minimum: float | None = None


def describe(settings: Sequence[Setting]) -> list[dict]:
    return [
        {
            "name": s.name,
            "question": s.question,
            "type": s.type,
            "default": s.default,
            "required": s.required,
            "choices": list(s.choices),
            "minimum": s.minimum,
        }
        for s in settings
    ]


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _validate(s: Setting, value: Any) -> Any:
    def bad(why: str) -> InputError:
        return InputError(f"invalid setting {s.name}: {why}")

    if s.type in ("string", "path"):
        if not isinstance(value, str) or not value:
            raise bad("expected a non-empty string")
    elif s.type == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            raise bad("expected an integer")
    elif s.type == "number":
        if not _is_number(value):
            raise bad("expected a number")
    elif s.type == "string_list":
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise bad("expected a list of strings")
    elif s.type == "account_id":
        if not isinstance(value, str) or not _ACCOUNT.fullmatch(value):
            raise bad("expected a 12-digit account id")
    elif s.type == "region":
        if not isinstance(value, str) or not _REGION.fullmatch(value):
            raise bad("expected an AWS region such as us-east-1")
    elif s.type == "choice":
        if value not in s.choices:
            raise bad(f"expected one of {', '.join(s.choices)}")
    if s.minimum is not None and s.type in ("integer", "number") and value < s.minimum:
        raise bad(f"must be at least {s.minimum}")
    return value


def load_config(path: Path, settings: Sequence[Setting]) -> dict[str, Any]:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as e:
        raise InputError(f"cannot read config {path}: {e.strerror or e}") from e
    except json.JSONDecodeError as e:
        raise InputError(f"config {path} is not valid JSON: {e}") from e
    if not isinstance(raw, dict):
        raise InputError("config must be a JSON object")
    known = {s.name: s for s in settings}
    for key in raw:
        if key not in known:
            raise InputError(f"unknown setting: {key}")
    config: dict[str, Any] = {}
    for s in settings:
        if s.name in raw:
            config[s.name] = _validate(s, raw[s.name])
        elif s.required:
            raise InputError(f"missing required setting: {s.name}")
        else:
            config[s.name] = s.default
    return config
