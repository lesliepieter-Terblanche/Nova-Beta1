# Social media posting with Postiz

Nova posts and schedules to your social accounts through [Postiz](https://github.com/gitroomhq/postiz-app), an
open-source scheduler (AGPL-3.0). Nova only talks to Postiz's public API; Postiz itself holds the logins to
Instagram, TikTok, LinkedIn, X, YouTube and the rest.

## Two ways to run Postiz

| | Cost | What it takes |
|---|---|---|
| **Your own Postiz** (self-hosted) | Free | Docker Desktop on the PC, and a developer app/key from each social network you want to post to. This is the fiddly part: Meta, TikTok, LinkedIn and X each have their own sign-up, and some need an app review before posts go public. |
| **Postiz cloud** (postiz.com) | Paid subscription | Nothing to install; you connect accounts in their web app. |

Follow Postiz's own, current instructions — they change between versions:

- Self-hosting with Docker: <https://docs.postiz.com/installation/docker-compose>
- Connecting each network: <https://docs.postiz.com/providers>
- The public API and its key: <https://docs.postiz.com/public-api/introduction>

## Connect Nova

1. In Postiz: **Settings → Public API** → copy the API key.
2. In Nova: **Settings → API keys → Postiz API key** → paste → **Test**. It lists your connected channels.
3. If you run your own Postiz, set **Settings → Files & web → Postiz address** to your backend address, for example
   `http://localhost:4007/api` (Nova adds `/public/v1`). Leave it alone for Postiz cloud.

## Use it

- "What social channels do I have?"
- "Post the Harbour Homes reel to Instagram and TikTok tomorrow at 6pm, caption: Wake up to the ocean."
- "Save that as a draft on LinkedIn."
- "What's scheduled this week?"

Nova **always asks for a yes** before she schedules or publishes anything. TikTok and YouTube need a video (.mp4).

## Limits

- Postiz allows a limited number of new posts and uploads per hour through the API (you can raise it on your own
  server with `API_LIMIT`).
- Each network's own rules still apply (video length, aspect ratio, daily post caps).
