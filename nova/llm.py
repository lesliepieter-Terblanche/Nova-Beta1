"""Hybrid LLM router: local Ollama first, cloud models (Gemini, Groq, Cerebras, Mistral, GitHub Models, xAI Grok)
as backup.

They all speak the OpenAI chat-completions API, so one client class covers them.
Cloud providers retire models regularly; when a model is gone, Nova picks the best
available one from the provider's own model list automatically.
Free tiers run out: a provider that says "rate limit" is rested for a while and the next one answers.
"""
from __future__ import annotations

import json
import os
import re
import time
import uuid
from dataclasses import dataclass, field

from openai import OpenAI

# Built-in providers, merged under config.yaml so new ones work without editing your config.
DEFAULT_PROVIDERS = {
    "gemini": {"base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
               "model": "gemini-2.5-flash", "key_env": "GEMINI_API_KEY", "timeout": 90},
    "groq": {"base_url": "https://api.groq.com/openai/v1", "model": "auto", "key_env": "GROQ_API_KEY", "timeout": 60},
    "cerebras": {"base_url": "https://api.cerebras.ai/v1", "model": "auto", "key_env": "CEREBRAS_API_KEY",
                 "timeout": 60},
    "mistral": {"base_url": "https://api.mistral.ai/v1", "model": "mistral-small-latest",
                "key_env": "MISTRAL_API_KEY", "timeout": 90},
    # GitHub Models: a GitHub token with the "models" permission. Its model list lives at catalog_url.
    "github": {"base_url": "https://models.github.ai/inference", "model": "openai/gpt-4o-mini",
               "key_env": "GITHUB_MODELS_TOKEN", "timeout": 90,
               "catalog_url": "https://models.github.ai/catalog/models"},
    "xai": {"base_url": "https://api.x.ai/v1", "model": "auto", "key_env": "XAI_API_KEY", "timeout": 90},
    # v2.38.2: the local model for deeper thinking (first in the "smart" list). Gemma 3 can't call tools in Ollama,
    # so it does the thinking and writing; requests that need a tool go to the next model in the list.
    "ollama_deep": {"base_url": "http://localhost:11434/v1", "model": "gemma3:4b", "timeout": 180},
}

# When a model is "auto" or has been retired, prefer these (first match in the provider's list wins).
MODEL_PREFERENCE = {
    "groq": ["llama-3.3-70b", "llama-4-maverick", "gpt-oss-120b", "kimi-k2", "llama-4-scout", "qwen3-32b",
             "gpt-oss-20b", "llama"],
    "xai": ["grok-4-fast-non-reasoning", "grok-4-fast", "grok-4", "grok-3-mini", "grok-3", "grok"],
    "gemini": ["gemini-2.5-flash", "gemini-flash-latest", "gemini-2.0-flash", "flash"],
    "cerebras": ["gpt-oss-120b", "llama-3.3-70b", "qwen-3", "llama-4", "llama", "qwen"],
    "mistral": ["mistral-small-latest", "mistral-medium-latest", "mistral-large-latest", "mistral-small", "mistral"],
    "github": ["openai/gpt-4o-mini", "openai/gpt-4.1-mini", "openai/gpt-4.1-nano", "openai/gpt-4.1", "openai/gpt-4o",
               "gpt-4", "mistral", "llama"],
}
NOT_CHAT = ("whisper", "guard", "tts", "embed", "image", "audio", "playai", "orpheus", "moderation",
            "imagine", "vision-preview", "transcribe", "prompt-guard", "compound", "ocr", "codestral", "voxtral")


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


def rest_seconds(e: Exception) -> int:
    """How long to leave a provider alone after this error: 0 = it wasn't a limit. A free tier that is used up
    for the day rests for half an hour; a per-minute limit for a minute (or what the provider asks for)."""
    t = str(e).lower()
    status = getattr(e, "status_code", None)
    if status != 429 and not any(k in t for k in ("rate limit", "rate_limit", "ratelimit", "too many requests",
                                                   "quota", "resource_exhausted", "tokens per", "requests per")):
        return 0
    try:
        after = float((getattr(getattr(e, "response", None), "headers", None) or {}).get("retry-after") or 0)
    except (TypeError, ValueError):
        after = 0.0
    if any(k in t for k in ("per day", "daily", "per_day", "tpd", "rpd", "quota", "month")):
        return int(max(after, 1800))
    return int(min(max(after, 60), 1800))


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
    def __init__(self, name: str, base_url: str, model: str, api_key: str, timeout: int, catalog_url: str = ""):
        self.name, self.model = name, model
        self.catalog_url, self._key = catalog_url, api_key
        self.rest_until = 0.0            # a free tier ran out: skipped until then
        self.local = "localhost" in base_url or "127.0.0.1" in base_url
        self.no_tools = False                      # learnt on the first try: this model can't call tools
        self.client = OpenAI(base_url=base_url, api_key=api_key, timeout=timeout, max_retries=1)
        self.switched_from = ""          # set when a retired model was replaced automatically
        self.no_stream = False           # set when this provider can't stream replies

    def resolve_model(self) -> str:
        """Swap a missing / 'auto' model for the best one this key can use."""
        if self.catalog_url:                 # its model list isn't at the usual /models
            import httpx
            r = httpx.get(self.catalog_url, headers={"Authorization": f"Bearer {self._key}"}, timeout=20)
            r.raise_for_status()
            data = r.json()
            ids = [str(m.get("id") or m.get("name")) for m in (data.get("data", data) if isinstance(data, dict) else data)]
        else:
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
            self.providers[name] = Provider(name, p["base_url"], p["model"], key, p.get("timeout", 60),
                                            p.get("catalog_url", ""))
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
        print(f"[llm] primary={self.primary} smart={self.smart or 'none (add a free Gemini, Groq, Cerebras or Mistral key in Settings)'}")

    def warm_up(self, quiet: bool = False) -> None:
        """Load the local models into memory now and keep them there, so the first command isn't slow."""
        import httpx
        keep = self.keep_alive
        everyday = self.providers.get(self.primary)
        if everyday is not None and not everyday.local:
            return     # a cloud model does the everyday thinking: leave the graphics card free; the local one loads if needed
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
        names = names + ["ollama"]        # the local model is always the last resort (offline, or every key failing)
        seen, out = set(), []
        for n in names:
            if n in self.providers and n not in seen:
                seen.add(n)
                out.append(self.providers[n])
        return out

    def chat(self, messages, tools=None, prefer_smart=False, temperature=0.3, on_delta=None) -> LLMReply:
        errors, resting = [], []
        for p in self.order(prefer_smart):
            if tools and p.no_tools:
                continue                             # it can think but not act: the next model takes this one
            if not p.local and p.rest_until > time.time():
                resting.append(p)                    # its free tier ran out a moment ago: the next one answers
                continue
            reply = self._try(p, messages, tools, temperature, on_delta, errors)
            if reply:
                return reply
        for p in resting:                            # everything else failed: the limit may have lifted
            reply = self._try(p, messages, tools, temperature, on_delta, errors)
            if reply:
                return reply
        raise RuntimeError("All models failed. " + " | ".join(errors))

    def _try(self, p: Provider, messages, tools, temperature, on_delta, errors: list) -> LLMReply | None:
        try:
            reply = p.chat(messages, tools, temperature, on_delta) if on_delta else p.chat(messages, tools, temperature)
            if not reply.content and not reply.tool_calls:
                raise ValueError("empty reply")
            self.last_provider = p.name
            p.rest_until = 0.0
            return reply
        except Exception as e:
            errors.append(f"{p.name}: {e}")
            if tools and "does not support tools" in str(e).lower():
                p.no_tools = True
                print(f"[llm] {p.name} ({p.model}) can't use tools -> it answers thinking and writing requests; "
                      "requests that need a tool go to the next model")
                return None
            rest = 0 if p.local else rest_seconds(e)
            if rest:
                p.rest_until = time.time() + rest
                print(f"[llm] {p.name} hit its limit -> resting it for {rest // 60 or 1} min, trying the next model")
            else:
                print(f"[llm] {p.name} failed -> {e}")
            return None

    def resting(self) -> dict[str, int]:
        """Providers being rested after a rate limit: {name: seconds left}."""
        now = time.time()
        return {n: int(p.rest_until - now) for n, p in self.providers.items() if p.rest_until > now}

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
