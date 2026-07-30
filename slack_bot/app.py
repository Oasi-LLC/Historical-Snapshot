from __future__ import annotations

import logging
import os
import re
import sys
from collections import OrderedDict
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_SRC = _ROOT / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from historical_snapshot.chat import handle_chat_message

LOGGER = logging.getLogger(__name__)

MENTION_PATTERN = re.compile(r"<@[^>]+>")

# Cap on how many recent Slack event ids we remember for de-duplication purposes
# (Slack Socket Mode can redeliver an event if the ack is slow or the connection
# blips; without this a single mention could produce multiple bot replies).
MAX_SEEN_EVENTS = 2000

GENERIC_ERROR_REPLY = (
    "Sorry, something went wrong while running that snapshot. "
    "I've logged the details — please try again in a moment or rephrase the question."
)


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def _allowed_channel_ids() -> set[str] | None:
    raw = os.environ.get("SLACK_ALLOWED_CHANNEL_IDS", "").strip()
    if not raw:
        return None
    return {item.strip() for item in raw.split(",") if item.strip()}


def _data_root() -> str:
    return os.environ.get("SNAPSHOT_DATA_ROOT", "data").strip() or "data"


def strip_bot_mention(text: str) -> str:
    return MENTION_PATTERN.sub("", text or "").strip()


def channel_allowed(channel_id: str) -> bool:
    allowed = _allowed_channel_ids()
    if allowed is None:
        return True
    return channel_id in allowed


def _is_bot_message(msg: dict, bot_user_id: str) -> bool:
    if msg.get("bot_id"):
        return True
    if msg.get("user") == bot_user_id:
        return True
    return bool(msg.get("subtype") == "bot_message")


def fetch_thread_conversation(
    client,
    *,
    channel_id: str,
    thread_ts: str,
    bot_user_id: str,
    exclude_ts: str | None = None,
) -> list[dict[str, str]]:
    try:
        response = client.conversations_replies(
            channel=channel_id,
            ts=thread_ts,
            limit=50,
        )
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("Could not fetch thread history: %s", exc)
        return []

    turns: list[dict[str, str]] = []
    for msg in response.get("messages", []):
        if exclude_ts and msg.get("ts") == exclude_ts:
            continue
        text = strip_bot_mention(msg.get("text", "")).strip()
        if not text or text == "Looking that up…":
            continue
        role = "assistant" if _is_bot_message(msg, bot_user_id) else "user"
        turns.append({"role": role, "content": text})
    return turns


def build_app() -> App:
    bot_token = _required_env("SLACK_BOT_TOKEN")
    app = App(token=bot_token)

    # Cached bot user id — resolved once instead of calling auth_test() on every
    # threaded message. Stored in a mutable holder so the closures below can
    # populate it lazily (e.g. if the startup lookup fails).
    bot_identity: dict[str, str | None] = {"user_id": None}

    def _resolve_bot_user_id(client) -> str | None:  # noqa: ANN001
        if bot_identity["user_id"] is None:
            try:
                auth = client.auth_test()
                bot_identity["user_id"] = auth.get("user_id")
            except Exception as exc:  # noqa: BLE001
                LOGGER.warning("Could not resolve bot user id: %s", exc)
        return bot_identity["user_id"]

    try:
        bot_identity["user_id"] = app.client.auth_test().get("user_id")
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("Could not resolve bot user id at startup, will retry lazily: %s", exc)

    # Recently-seen Slack event ids, to avoid double-processing a redelivered
    # app_mention (Slack Socket Mode will retry if the ack is slow).
    seen_events: "OrderedDict[str, None]" = OrderedDict()

    def _already_processed(event_key: str) -> bool:
        if event_key in seen_events:
            return True
        seen_events[event_key] = None
        if len(seen_events) > MAX_SEEN_EVENTS:
            seen_events.popitem(last=False)
        return False

    @app.event("app_mention")
    def handle_app_mention(event, say, client, logger, body):  # noqa: ANN001
        channel_id = event.get("channel", "")
        if not channel_allowed(channel_id):
            logger.info("Ignored mention in disallowed channel: %s", channel_id)
            return

        event_key = str(
            (body or {}).get("event_id") or f"{channel_id}:{event.get('ts')}"
        )
        if _already_processed(event_key):
            logger.info("Ignoring duplicate/redelivered Slack event: %s", event_key)
            return

        message = strip_bot_mention(event.get("text", ""))
        thread_ts = event.get("thread_ts") or event.get("ts")
        user_id = event.get("user", "unknown")

        conversation: list[dict[str, str]] | None = None
        thread_parent = event.get("thread_ts")
        if thread_parent:
            bot_user_id = _resolve_bot_user_id(client)
            if bot_user_id:
                conversation = fetch_thread_conversation(
                    client,
                    channel_id=channel_id,
                    thread_ts=thread_parent,
                    bot_user_id=bot_user_id,
                    exclude_ts=event.get("ts"),
                )

        if not message:
            say(
                text=(
                    "Ask me about property performance. Examples:\n"
                    "• `Onera July 31 2026`\n"
                    "• `How is WMB pacing for Labor Day 2026?`\n"
                    "• `Diamond at Onera on July 31 2026`"
                ),
                thread_ts=thread_ts,
            )
            return

        logger.info("Processing mention from user=%s channel=%s", user_id, channel_id)
        say(text="Looking that up…", thread_ts=thread_ts)

        try:
            result = handle_chat_message(
                message,
                data_root=_data_root(),
                conversation=conversation,
                channel_id=channel_id,
                thread_ts=thread_ts,
                user_id=user_id,
            )
            say(text=result.reply, thread_ts=thread_ts, mrkdwn=True)
        except Exception:
            logger.exception("Snapshot chat failed")
            say(
                text=GENERIC_ERROR_REPLY,
                thread_ts=thread_ts,
            )

    return app


def _sync_on_startup_enabled() -> bool:
    return os.environ.get("SYNC_ON_STARTUP", "true").strip().lower() not in (
        "0",
        "false",
        "no",
    )


def _run_startup_sync() -> None:
    from historical_snapshot.service import ensure_daily_sync

    result = ensure_daily_sync(data_root=_data_root())
    if result is None:
        return
    for error in result.get("errors", []):
        LOGGER.error(
            "Startup sync error for %s: %s",
            error.get("folder"),
            error.get("error"),
        )


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    _required_env("SLACK_BOT_TOKEN")
    app_token = _required_env("SLACK_APP_TOKEN")
    allowed = _allowed_channel_ids()

    LOGGER.info("Starting Historical Snapshot Slack bot (Socket Mode)")
    LOGGER.info("Data root: %s", _data_root())
    if allowed:
        LOGGER.info("Allowed channels: %s", ", ".join(sorted(allowed)))
    else:
        LOGGER.info("Allowed channels: all (set SLACK_ALLOWED_CHANNEL_IDS to restrict)")

    if _sync_on_startup_enabled():
        _run_startup_sync()
    else:
        LOGGER.info("Startup sync disabled (SYNC_ON_STARTUP=false)")

    app = build_app()
    SocketModeHandler(app, app_token).start()


if __name__ == "__main__":
    main()
