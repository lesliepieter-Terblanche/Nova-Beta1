"""Hybrid LLM router: local Ollama first, Groq free tier as fallback.

Both speak the OpenAI chat-completions API, so one client class covers both.
"""
from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import dataclass, field

from openai import OpenAI


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class LLMReply:
    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    provider: str = ""


class Provider:
    def __init__(self, name: str, base_url: str, model: str, api_key: str, timeout: int):
        self.name, self.model = name, model
        self.client = OpenAI(base_url=base_url, api_key=api_key, timeout=timeout, max_retries=1)

    def chat(self, messages, tools=None, temperature=0.3) -> LLMReply:
        kwargs = dict(model=self.model, messages=messages, temperature=temperature)
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        resp = self.client.chat.completions.create(**kwargs)
        msg = resp.choices[0].message
        calls = []
        for c in msg.tool_calls or []:
            try:
                args = json.loads(c.function.arguments or "{}")
            except json.JSONDecodeError:
                raise ValueError(f"{self.name} returned malformed tool arguments")
            calls.append(ToolCall(c.id or uuid.uuid4().hex[:8], c.function.name, args or {}))
        content = msg.content or ""
        if not calls and tools:
            calls = _salvage_tool_call(content, {t["function"]["name"] for t in tools})
            if calls:
                content = ""
        return LLMReply(content.strip(), calls, self.name)


def _salvage_tool_call(text: str, names: set[str]) -> list[ToolCall]:
    """Small models sometimes print the tool call as JSON instead of calling it."""
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return []
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return []
    name = data.get("name") or data.get("function")
    args = data.get("arguments") or data.get("parameters") or {}
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except json.JSONDecodeError:
            args = {}
    if name in names:
        return [ToolCall(uuid.uuid4().hex[:8], name, args)]
    return []


class LLM:
    def __init__(self, cfg):
        c = cfg.llm
        self.providers: dict[str, Provider] = {}
        for name, p in c.providers.items():
            key_env = p.get("key_env")
            key = os.environ.get(key_env, "").strip() if key_env else "local"
            if not key:
                continue      # no API key -> provider skipped
            self.providers[name] = Provider(name, p["base_url"], p["model"], key, p.get("timeout", 60))
        self.primary = c.primary
        self.smart = [n for n in c.get("smart", []) if n in self.providers]
        vp = c.get("vision_providers") or ([c["vision_provider"]] if c.get("vision_provider") else [])
        self.vision_order = [v for v in vp if v in self.providers]
        self.escalate_keywords = [k.lower() for k in c.get("escalate_keywords", [])]
        print(f"[llm] primary={self.primary} smart={self.smart or 'none (add GEMINI_API_KEY / GROQ_API_KEY)'}")

    def should_escalate(self, text: str) -> bool:
        low = text.lower()
        return bool(self.smart) and any(k in low for k in self.escalate_keywords)

    def order(self, prefer_smart: bool) -> list[Provider]:
        names = ([self.primary] + self.smart) if not prefer_smart else (self.smart + [self.primary])
        seen, out = set(), []
        for n in names:
            if n in self.providers and n not in seen:
                seen.add(n)
                out.append(self.providers[n])
        return out

    def chat(self, messages, tools=None, prefer_smart=False, temperature=0.3) -> LLMReply:
        errors = []
        for p in self.order(prefer_smart):
            try:
                reply = p.chat(messages, tools, temperature)
                if not reply.content and not reply.tool_calls:
                    raise ValueError("empty reply")
                return reply
            except Exception as e:
                errors.append(f"{p.name}: {e}")
                print(f"[llm] {p.name} failed -> {e}")
        raise RuntimeError("All models failed. " + " | ".join(errors))

    def complete(self, prompt: str, system: str = "", prefer_smart=True, temperature=0.5) -> str:
        """Plain text generation (used by skills, e.g. website builder)."""
        msgs = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
        return self.chat(msgs, None, prefer_smart, temperature).content

    def see(self, image_path: str, question: str) -> str:
        """Ask a vision model about an image. Tries each provider in `vision_providers` in order
        (e.g. Gemini first, then local Gemma 3 via Ollama), so vision keeps working offline."""
        import base64
        import io
        from PIL import Image
        img = Image.open(image_path).convert("RGB")
        img.thumbnail((1280, 1280))              # smaller = faster, and plenty for understanding
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=88)
        b64 = base64.b64encode(buf.getvalue()).decode()
        msgs = [{"role": "user", "content": [
            {"type": "text", "text": question},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
        ]}]
        errors = []
        for name in self.vision_order:
            p = self.providers.get(name)
            if not p:
                continue
            try:
                reply = p.chat(msgs)
                if reply.content:
                    return reply.content
            except Exception as e:
                errors.append(f"{name}: {e}")
                print(f"[vision] {name} failed -> {e}")
        if not errors:
            return ("No vision model available. Add GEMINI_API_KEY to .env, or run `ollama pull gemma3:4b` "
                    "for local vision.")
        return "Vision failed: " + " | ".join(errors)
