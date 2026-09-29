---
name: morning-briefing
description: A short spoken rundown of today — calendar, important email, tasks and reminders
triggers: [morning briefing, brief me, good morning, what's my day, whats my day, plan my day]
tool_groups: [google, system, brain, web]
---
1. Call get_time.
2. Call calendar_events for "today". Note the first meeting and anything that clashes.
3. Call gmail_search with "is:unread in:inbox newer_than:1d -category:promotions -category:social" (max 8).
   Pick at most three that look important (from real people, deadlines, money, customers).
4. Call tasks_list and list_reminders.
   If you know the user's home city, web_search "weather today <city>" and keep one line (high, rain chance).
5. Speak the briefing in under 45 seconds: greeting, weather, number of meetings and the first one, the top emails
   (sender + one-line gist), tasks due today, then ask "Anything you want me to handle first?"
If Google isn't connected, skip those steps and say so once.
