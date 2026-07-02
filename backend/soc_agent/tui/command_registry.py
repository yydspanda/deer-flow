"""Slash-command registry for the SOC review TUI."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class Command:
    name: str
    description: str


@dataclass(frozen=True)
class Resolution:
    kind: Literal["builtin", "unknown"]
    name: str = ""
    args: str = ""


BUILTIN_COMMANDS: tuple[Command, ...] = (
    Command("help", "Show review commands"),
    Command("refresh", "Reload open review queue"),
    Command("open", "Open investigation context: /open REV-..."),
    Command("close", "Close item: /close REV-... reason"),
    Command("correct", "Correct run: /correct RUN-... verdict reason"),
    Command("quit", "Exit the TUI"),
)

_BUILTIN_NAMES = frozenset(command.name for command in BUILTIN_COMMANDS)


def filter_commands(query: str) -> list[Command]:
    q = query.strip().lower()
    if not q:
        return list(BUILTIN_COMMANDS)
    prefix: list[Command] = []
    substring: list[Command] = []
    description: list[Command] = []
    for command in BUILTIN_COMMANDS:
        name = command.name.lower()
        if name.startswith(q):
            prefix.append(command)
        elif q in name:
            substring.append(command)
        elif q in command.description.lower():
            description.append(command)
    return prefix + substring + description


def resolve(text: str) -> Resolution:
    stripped = text.strip()
    if not stripped.startswith("/"):
        return Resolution(kind="unknown", name=stripped)

    body = stripped[1:]
    name, _, args = body.partition(" ")
    name = name.strip()
    args = args.strip()
    if name in _BUILTIN_NAMES:
        return Resolution(kind="builtin", name=name, args=args)
    return Resolution(kind="unknown", name=name, args=args)
