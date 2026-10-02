"""Hybrid LLM router: local Ollama first, cloud models (Gemini, Groq, xAI Grok) as backup.

They all speak the OpenAI chat-completions API, so one client class covers them.
Cloud providers retire models regularly; when a model is gone, Nova picks the best
available one from the provider's own model list automatically.
"""
from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import dataclass, field

from openai import OpenAI

# Built-in providers, merged under config.yaml so new ones work without editing your config.
DEFAULT_PROVIDERS = {
    "gemini": {"base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
               "model": "gemini-2.5-flash", "key_env": "GEMINI_API_KEY", "timeout": 90},
    "groq": {"base_url": "https://api.groq.com/openai/v1", "model": "auto", "key_env": "GROQ_API_KEY", "timeout": 60},
    "xai": {"base_url": "https://api.x.ai/v1", "model": "auto", "key_env": "XAI_API_KEY", "timeout": 90},
}

# When a model is "auto" or has been retired, prefer these (first match in the provider's list wins).
MODEL_PREFERENCE = {
    "groq": ["llama-3.3-70b", "llama-4-maverick", "gpt-oss-120b", "kimi-k2", "llama-4-scout", "qwen3-32b",
             "gpt-oss-20b", "llama"],
    "xai": ["grok-4-fast-non-reasoning", "grok-4-fast", "grok-4", "grok-3-mini", "grok-3", "grok"],
    "gemini": ["gemini-2.5-flash", "gemini-flash-latest", "gemini-2.0-flash", "flash"],
}
NOT_CHAT = ("whisper", "guard", "tts", "embed", "image", "audio", "playai", "orpheus", "moderation",
            "imagine", "vision-preview", "transcribe", "prompt-guard", "compound")


def pick_model(ids: list[str], provider: str, wanted: str = "") -> str:
    """Choose a chat model from a provider's live model list."""
    clean = {i.removeprefix("models/"): i for i in ids}
    if wanted and wanted != "auto" and wanted in clean:
        return clean[wanted]
    chat = [i for i in clean if not any(x in i.lower() for x in NOT_CHAT)]
    for pref in MODEL_PREFERENCE.get(provider, []):
        for i in sorted(chat):
            if pref in i.lower():
                return clean[i].removeprefix("models/")
    if not chat:
        raise ValueError(f"{provider} has no chat models available for this key")
    return sorted(chat)[0]


def _model_missing(e: Exception) -> bool:
    t = str(e).lower()
    return "model" in t and any(k in t for k in ("not_found", "does not exist", "decommission", "not found",
                                                   "no longer supported", "deprecated", "invalid model"))


def provider_config(cfg, name: str) -> dict | None:
    """config.yaml settings for a provider, on top of the built-in defaults."""
    user = dict((cfg.llm.get("providers") or {}).get(name) or {})
    base = DEFAULT_PROVIDERS.get(name)
    if not user and not base:
        return None
    return {**(base or {}), **user}


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
        self.local = "localhost" in base_url or "127.0.0.1" in base_url
        self.client = OpenAI(base_url=base_url, api_key=api_key, timeout=timeout, max_retries=1)
        self.switched_from = ""          # set when a retired model was replaced automatically
        self.no_stream = False           # set when this provider can't stream replies

    def resolve_model(self) -> str:
        """Swap a missing / 'auto' model for the best one this key can use."""
        ids = [m.id for m in self.client.models.list()]
        new = pick_model(ids, self.name, self.model)
        if new != self.model:
            print(f"[llm] {self.name}: using model '{new}' (was '{self.model}')")
            self.switched_from, self.model = self.model, new
        return new

    def chat(self, messages, tools=None, temperature=0.3, on_delta=None) -> LLMReply:
        if self.model in ("", "auto"):
            self.resolve_model()
        try:
            return self._chat(messages, tools, temperature, on_delta)
        except Exception as e:
            if self.local or not _model_missing(e):
                raise
            old = self.model
            if self.resolve_model() == old:
                raise
            return self._chat(messages, tools, temperature, on_delta)

    def _chat_stream(self, kwargs: dict, tools, on_delta) -> LLMReply:
        """The same call, streamed: text is passed on as it is written (so the voice can start with the first
        sentence). Tool calls arrive in pieces and are put together here."""
        content, calls, sent = "", {}, 0
        for chunk in self.client.chat.completions.create(stream=True, **kwargs):
            if not chunk.choices:
                continue
            d = chunk.choices[0].delta
            for c in getattr(d, "tool_calls", None) or []:
                slot = calls.setdefault(c.index if c.index is not None else len(calls), {"id": "", "name": "", "args": ""})
                slot["id"] = c.id or slot["id"]
                if c.function:
                    slot["name"] += c.function.name or ""
                    slot["args"] += c.function.arguments or ""
            piece = getattr(d, "content", None) or ""
            if piece:
                content += piece
                # hold back anything that looks like a tool call written as text, and text that comes with tool calls
                if not calls and len(content) >= 3 and not content.lstrip().startswith(("{", "[", "```", "<")):
                    on_delta(content[sent:])
                    sent = len(content)
        out = []
        for slot in calls.values():
            try:
                args = json.loads(slot["args"] or "{}")
            except json.JSONDecodeError:
                raise ValueError(f"{self.name} returned malformed tool arguments")
            out.append(ToolCall(slot["id"] or uuid.uuid4().hex[:8], slot["name"], args or {}))
        if not out and tools:
            out = _salvage_tool_call(content, {t["function"]["name"] for t in tools})
            if out:
                content = ""
        if not out and sent < len(content):
            on_delta(content[sent:])
        return LLMReply(content.strip(), out, self.name)

    def _chat(self, messages, tools=None, temperature=0.3, on_delta=None) -> LLMReply:
        kwargs = dict(model=self.model, messages=messages, temperature=temperature)
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        if on_delta is not None and not self.no_stream:
            said = []
            try:
                return self._chat_stream(kwargs, tools, lambda t: (said.append(1), on_delta(t)))
            except Exception as e:
                if said:                         # part of it was already spoken: don't start over
                    raise
                print(f"[llm] {self.name}: streaming not available ({str(e)[:120]}); answering in one piece")
                self.no_stream = True
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
        for name in dict.fromkeys([*DEFAULT_PROVIDERS, *(c.get("providers") or {})]):
            p = provider_config(cfg, name)
            key_env = p.get("key_env")
            key = os.environ.get(key_env, "").strip() if key_env else "local"
            if not key:
                continue      # no API key -> provider skipped
            self.providers[name] = Provider(name, p["base_url"], p["model"], key, p.get("timeout", 60))
        self.primary = c.primary
        self.smart = [n for n in c.get("smart", []) if n in self.providers]
        # a cloud key you've added but not listed still gets used, as the last backup
        self.smart += [n for n, p in self.providers.items()
                       if not p.local and n not in self.smart and n != self.primary]
        vp = c.get("vision_providers") or ([c["vision_provider"]] if c.get("vision_provider") else [])
        self.vision_order = [v for v in vp if v in self.providers]
        self.escalate_keywords = [k.lower() for k in c.get("escalate_keywords", [])]
        self.keep_alive = str(c.get("keep_alive", "24h"))
        self.last_provider = ""
        print(f"[llm] primary={self.primary} smart={self.smart or 'none (add a Gemini, Groq or xAI key in Settings)'}")

    def warm_up(self, quiet: bool = False) -> None:
        """Load the local models into memory now and keep them there, so the first command isn't slow."""
        import httpx
        keep = self.keep_alive
        first = [self.providers[n] for n in [self.primary] if n in self.providers]
        for p in first + list(self.providers.values()):
            if not p.local:
                continue
            base = str(p.client.base_url).rstrip("/").removesuffix("/v1")
            try:
                httpx.post(f"{base}/api/generate", json={"model": p.model, "keep_alive": keep}, timeout=180)
                if not quiet:
                    print(f"[llm] {p.name} ({p.model}) loaded and kept ready")
            except Exception as e:
                if not quiet:
                    print(f"[llm] couldn't pre-load {p.model}: {e}")
            break      # only the everyday local model; vision loads on demand

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

    def chat(self, messages, tools=None, prefer_smart=False, temperature=0.3, on_delta=None) -> LLMReply:
        errors = []
        for p in self.order(prefer_smart):
            try:
                reply = p.chat(messages, tools, temperature, on_delta) if on_delta else p.chat(messages, tools, temperature)
                if not reply.content and not reply.tool_calls:
                    raise ValueError("empty reply")
                self.last_provider = p.name
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
