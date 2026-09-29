# Contributing to Nova

Thanks for helping! The most useful contributions:

1. **Playbooks.** Plain-English procedures in `playbooks/`. No code needed.
2. **Plugins.** New abilities as single files in `plugins/`.
3. **MCP recipes.** Tested `mcp_servers` entries for `config.example.yaml` + a row in `docs/MCP.md`.
4. **Fixes, docs, and macOS/Linux support.**

## Dev setup

```bat
git clone https://github.com/lesliepieter-Terblanche/Nova-Beta1.git
cd Nova-Beta1
py -3.11 -m venv .venv && .venv\Scripts\activate
pip install -r requirements.txt -r requirements-dev.txt
pytest -q
ruff check .
```

The tests use a fake LLM and a fake voice, so they need no GPU, keys, microphone or internet.
CI runs them on Windows and Ubuntu with the lightweight `requirements-ci.txt`.

## Guidelines

- **Local and free first.** A cloud service should be optional and have a local fallback where possible.
- **Safe by default.** Anything that sends, deletes, buys, posts, runs commands or changes settings uses
  `confirm=True`. Respect `files.allowed_roots`.
- **Small-model friendly.** Short tool descriptions, few parameters, compact outputs, good `register_group` keywords.
- **Lazy imports.** Import heavy libraries inside the tool function so Nova starts even if they're missing.
- **Settings go in config.** Add new options to `config.example.yaml` and `docs/CONFIGURATION.md`.
- **Regenerate the tool docs** after changing tools: `python scripts/gen_tool_docs.py`.
- Never commit `.env`, `config.yaml`, `secrets/`, `data/`, `workspace/` or model files.

## Pull requests

Branch from `main`, keep PRs focused, fill in the template, make sure CI is green.
Add a line to `CHANGELOG.md` under **Unreleased**.
