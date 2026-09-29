"""Tool registry. Decorate a function with @tool(...) and the LLM can call it.

The JSON schema is built from type hints; parameter descriptions come from
an "Args:" block in the docstring.
"""
from __future__ import annotations

import inspect
import json
import re
import traceback
import typing
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class Tool:
    name: str
    description: str
    func: Callable[..., Any]
    parameters: dict
    group: str
    confirm: bool = False           # needs a "yes" from the user before running
    keywords: list[str] = field(default_factory=list)

    def schema(self) -> dict:
        return {
            "type": "function",
            "function": {"name": self.name, "description": self.description, "parameters": self.parameters},
        }

    def run(self, args: dict) -> str:
        sig = inspect.signature(self.func)
        takes_any = any(p.kind is inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())
        clean = dict(args or {}) if takes_any else {k: v for k, v in (args or {}).items() if k in sig.parameters}
        try:
            result = self.func(**clean)
        except Exception as e:  # tools never crash the agent
            traceback.print_exc()
            return f"ERROR in {self.name}: {type(e).__name__}: {e}"
        if result is None:
            return "Done."
        if isinstance(result, str):
            return result
        return json.dumps(result, ensure_ascii=False, default=str, indent=1)


REGISTRY: dict[str, Tool] = {}
GROUP_KEYWORDS: dict[str, list[str]] = {}

_PY_TO_JSON = {str: "string", int: "integer", float: "number", bool: "boolean"}


def _json_type(annotation) -> dict:
    origin = typing.get_origin(annotation)
    if origin is typing.Union or str(origin) == "types.UnionType":
        args = [a for a in typing.get_args(annotation) if a is not type(None)]
        return _json_type(args[0]) if args else {"type": "string"}
    if origin in (list, typing.List):
        (inner,) = typing.get_args(annotation) or (str,)
        return {"type": "array", "items": _json_type(inner)}
    return {"type": _PY_TO_JSON.get(annotation, "string")}


def _parse_doc(doc: str) -> tuple[str, dict[str, str]]:
    doc = inspect.cleandoc(doc or "")
    summary, _, rest = doc.partition("Args:")
    params = {}
    for line in rest.splitlines():
        m = re.match(r"\s*(\w+)\s*:\s*(.+)", line)
        if m:
            params[m.group(1)] = m.group(2).strip()
    return " ".join(summary.split()), params


def tool(group: str, confirm: bool = False, name: str | None = None):
    def deco(fn):
        description, pdocs = _parse_doc(fn.__doc__)
        hints = typing.get_type_hints(fn)
        props, required = {}, []
        for pname, p in inspect.signature(fn).parameters.items():
            spec = _json_type(hints.get(pname, str))
            if pname in pdocs:
                spec["description"] = pdocs[pname]
            props[pname] = spec
            if p.default is inspect.Parameter.empty:
                required.append(pname)
        params = {"type": "object", "properties": props, "required": required}
        t = Tool(name or fn.__name__, description, fn, params, group, confirm)
        REGISTRY[t.name] = t
        return fn

    return deco


def register_group(group: str, keywords: list[str]) -> None:
    GROUP_KEYWORDS[group] = [k.lower() for k in keywords]


def select_tools(text: str, always: tuple[str, ...] = ("system",), extra: set[str] | None = None) -> list[Tool]:
    """Pick the tool groups relevant to this request so small local models
    aren't flooded with 60 tool definitions."""
    low = text.lower()
    groups = set(always)
    for g, kws in GROUP_KEYWORDS.items():
        if any(k in low for k in kws):
            groups.add(g)
    if groups == set(always) and not extra:     # nothing obvious -> general-purpose set
        groups |= {"brain", "web"}
    groups |= set(extra or ())
    return [t for t in REGISTRY.values() if t.group in groups]
