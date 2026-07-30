# Slack Snapshot Bot Setup

## Bot token scopes

| Scope | Why |
|-------|-----|
| `app_mentions:read` | Receive `@Snapshot Bot` mentions |
| `chat:write` | Post replies |
| `channels:history` | Read thread replies (public channels) |
| `groups:history` | Read thread replies (private channels) |

## Socket Mode

1. Enable Socket Mode on the Slack app
2. Add scope `connections:write` to the app-level token
3. Subscribe to bot event: `app_mention`
4. Install to workspace; copy `xoxb-` bot token and `xapp-` app token

## Environment

Copy `.env.example` → `.env` and set:

- `SLACK_BOT_TOKEN`
- `SLACK_APP_TOKEN`
- `ANTHROPIC_API_KEY`
- Optional: `SLACK_ALLOWED_CHANNEL_IDS` (comma-separated)

## Run

```bash
chmod +x scripts/run_slack_bot.sh
./scripts/run_slack_bot.sh
```

Invite the bot to a channel, then `@Snapshot Bot Onera July 31 2026` or ask about a holiday/weekend (bot will clarify with suggested dates).
