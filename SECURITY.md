# Security

Nova can read your email, control your browser and run commands on your PC. Treat its setup like a password manager's.

## What stays on your PC

| Data | Location |
|---|---|
| Memories, notes index, activity log | `data/nova.db` |
| Notes | `data/brain/` |
| Browser logins (Nova's own profile) | `data/browser_profile/` |
| Google OAuth token | `secrets/token.json` |
| API keys | `.env` |
| Everything Nova creates | `workspace/` |

All of these are git-ignored. **Back up `data/`**. It *is* Nova's memory.

## What leaves your PC

- Request text + relevant memories → **Gemini / Groq** when a request escalates or the local model fails.
  Set `llm.smart: []` to prevent this.
- Images → **Gemini** for vision (screen, photos, webcam, ads). Use a local vision model to prevent this.
- Reply text → **ElevenLabs** to speak it. Set `tts.engine: piper` to prevent this.
- Messages and files → **Telegram** when you use the bot.
- Whatever you ask Nova to send to Google, websites or MCP servers.

## Built-in protections

- Telegram answers only `allowed_user_ids`.
- The dashboard binds to `127.0.0.1`. Don't expose it to the internet. Use Tailscale for phone access.
- File tools only work inside `files.allowed_roots`. Deletes go to the Recycle Bin.
- Sending email, deleting/moving files, shell commands, power actions, browser submissions, updates, rollback
  and write-type MCP tools all require a spoken or typed **yes**.
- The Nova MCP server never exposes confirmation-gated tools.

## Recommendations

- Keep `allowed_user_ids` to yourself, and keep `system.allow_shell: false` if you don't need remote commands.
- Install only MCP servers you trust. They run with your Windows user's permissions.
- Use a separate Google Cloud project for Nova and revoke it at myaccount.google.com/permissions if the PC is lost.
- Keep the GitHub repo **private** if it contains anything personal (config is excluded by default).

## Reporting a vulnerability

Please report security issues privately via GitHub **Security → Report a vulnerability** on this repository
rather than opening a public issue.
