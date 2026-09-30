"""Runs one browser-use task. Started by Nova with the browser-agent's own Python (tools/browser-use/venv), so
browser-use's pinned packages never clash with Nova's. Reads a JSON job file, prints one JSON line per step and a
final {"done": …} line. Stops cleanly when the job's stop-file appears.

Standalone on purpose: it must not import anything from Nova.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

os.environ.setdefault("ANONYMIZED_TELEMETRY", "false")
os.environ.setdefault("BROWSER_USE_CLOUD_SYNC", "false")
os.environ.setdefault("BROWSER_USE_SETUP_LOGGING", "false")


def say(**kw) -> None:
    print(json.dumps(kw, ensure_ascii=False), flush=True)


def make_llm(job: dict):
    import browser_use as bu
    p, model, key = job["provider"], job.get("model", ""), os.environ.get("NOVA_WEB_AGENT_KEY", "")
    if p == "gemini":
        return bu.ChatGoogle(model=model or "gemini-2.5-flash", api_key=key)
    if p == "groq":
        return bu.ChatGroq(model=model or "meta-llama/llama-4-maverick-17b-128e-instruct", api_key=key)
    if p == "openai":
        return bu.ChatOpenAI(model=model or "gpt-4.1-mini", api_key=key, base_url=job.get("base_url") or None)
    if p == "ollama":
        return bu.ChatOllama(model=model or "qwen2.5:7b", host=job.get("base_url") or "http://localhost:11434")
    raise SystemExit(f"unknown provider {p}")


async def main(job_path: str) -> None:
    job = json.loads(Path(job_path).read_text(encoding="utf-8"))
    stop_file = Path(job["stop_file"])
    from browser_use import Agent, BrowserProfile

    profile_kw = {"user_data_dir": job["profile_dir"], "headless": bool(job.get("headless", False)),
                  "keep_alive": False}
    if job.get("executable_path"):
        profile_kw["executable_path"] = job["executable_path"]
    profile_kw.update(job.get("profile") or {})              # extra BrowserProfile settings (web_agent.profile)
    profile = BrowserProfile(**profile_kw)

    async def step(state, output, n):
        goal = getattr(output, "next_goal", "") or getattr(getattr(output, "current_state", None), "next_goal", "")
        say(step=n, goal=str(goal)[:200], url=str(getattr(state, "url", ""))[:200])

    async def should_stop() -> bool:
        return stop_file.exists()

    agent = Agent(task=job["task"], llm=make_llm(job), browser_profile=profile,
                  extend_system_message=job.get("rules", ""), register_new_step_callback=step,
                  register_should_stop_callback=should_stop, use_judge=False, calculate_cost=False,
                  available_file_paths=job.get("files") or None)
    try:
        history = await agent.run(max_steps=int(job.get("max_steps", 30)))
        say(done=True, result=history.final_result() or "", success=history.is_successful(),
            stopped=stop_file.exists(), steps=len(getattr(history, "history", []) or []))
    except Exception as e:                                   # report, don't crash silently
        say(done=True, result="", success=False, error=f"{type(e).__name__}: {e}")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))
