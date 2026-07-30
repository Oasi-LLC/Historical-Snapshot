# Snapshot Bot — Agent Context

## Architecture

```
Slack app_mention (thread → fetch history + load thread state)
  → handle_chat_message(channel_id, thread_ts, conversation=...)
       → try_thread_turn (confirm / follow-up / date override — Python)
       → else rules parser or Claude tool loop
            → clarify (suggest dates, save pending_query to thread state)
            → resolve_and_run_snapshot (save last_query to thread state)
       → reply in thread (footer embeds query JSON)
```

## Stay windows — no hardcoded holidays

Holidays and relative weekends are **not** resolved in Python. Claude:
1. Suggests stay dates in `clarify.proposed`
2. Waits for user confirmation
3. Runs snapshot with explicit ISO dates

## Prior-year compare

- **Default:** calendar `shift_one_year` (omit `prior_start_date` / `prior_end_date`)
- **Moving holidays:** Claude suggests custom LY window in clarify when fair compare differs
  (e.g. Labor Day 2026 Sep 4–7 → LY Aug 29–Sep 1 2025)
- After confirmation, pass `prior_start_date` / `prior_end_date` on snapshot call

## Key files

| File | Role |
|------|------|
| `src/historical_snapshot/chat/orchestrator.py` | Rules + Claude routing |
| `src/historical_snapshot/chat/tools.py` | `list_properties`, `resolve_and_run_snapshot`, `clarify` |
| `src/historical_snapshot/chat/prompts.py` | RM doctrine, clarify-then-run rules |
| `src/historical_snapshot/chat/llm_claude.py` | Tool loop, thread conversation |
| `slack_bot/app.py` | Socket Mode, `conversations.replies` for threads |

## Slack scopes

`app_mentions:read`, `chat:write`, `channels:history`, `groups:history`

## Env

`ANTHROPIC_API_KEY`, `CLAUDE_MODEL`, `SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN`

Google Sheets sync (startup): `GOOGLE_SHEETS_SPREADSHEET_ID`, `GOOGLE_APPLICATION_CREDENTIALS`

Optional:

| Variable | Default | Purpose |
|----------|---------|---------|
| `SYNC_ON_STARTUP` | `true` | Pull Sheets on bot start; set `false` to skip |
| `SYNC_TIMEZONE` | `Europe/Lisbon` | Calendar day for “already synced today” dedup |
| `SNAPSHOT_DATA_ROOT` | `data` | Cache directory |

## Data sync on startup

When the Slack bot starts (`./scripts/run_slack_bot.sh`), it runs a Google Sheets sync **once per calendar day** (Europe/Lisbon by default). Restarts the same day skip the pull if every Sheets-backed property already has today’s `synced_at` in `data/.cache/sheets/*.meta.json`.

- Partial sync failures retry on the next startup (any property not synced today triggers a full sync).
- Sync errors are logged; the bot still starts with the existing cache.
- Manual `snapshot sync` and `POST /sync` are unchanged.

## Follow-ups in thread (Phase 1)

Thread state is persisted per `(channel_id, thread_ts)` in `data/.cache/threads/` and embedded
in each bot reply as a hidden HTML comment footer (`<!-- snapshot:{...} -->`).

| Turn type | Example | Python behavior |
|-----------|---------|-----------------|
| Confirm | `yes` after Clarification | Runs `pending_query` from state — no Claude date guessing |
| Follow-up | `top listings`, `same window`, or similar phrasing | Regex or **intent router** re-runs `last_query` |
| Date override | `use Nov 24-27 instead` | Merges new dates into `last_query` |
| Listing drill-down | `Gallery House` | Filters snapshot to that unit |
| Interpret only | `why are we behind?` | Re-runs same query, Claude writes Interpretation only |
| New topic | Different property | Falls through to full Claude flow |

Portfolio snapshots show the performance table only. Listing/channel breakdown tables appear
when the user asks for them in thread (or Claude passes `listing_breakdown` / `channel_breakdown`).
Thread state is stored on disk — not embedded in Slack messages.

## Delta reply modes (Phase 2)

Thread follow-ups avoid reposting the full portfolio table when the stay window is unchanged.

| Mode | When | Slack output |
|------|------|----------------|
| `full` | Confirm, date override, first snapshot | Full tables + Interpretation |
| `delta_listings` | Listing follow-up, same window | Header + top-listings tables only |
| `delta_channels` | Channel follow-up | Header + channel mix tables |
| `delta_listing` | Single-unit drill-down | Unit-scoped performance table |
| `interpretation_only` | “Why / explain” on same query | Interpretation only |

Implemented in `reply_modes.py`, `formatter.build_formatted_report()`, and `thread_turn.py`.

## Flexible output / AnswerPlan (sketch)

Reply modes were a closed set of full report templates. Freeform questions need output shaped
to the ask. The sketch introduces a compositional **AnswerPlan**:

```
question
  → AnswerPlan { intent, basis, blocks[], listings[], fields[] }
  → fetch snapshot fields from the plan
  → render only the requested blocks
  → interpretation scoped by plan.interpretation_mode
```

| Piece | File | Role |
|-------|------|------|
| Schema | `answer_plan.py` | `AnswerPlan`, `plan_from_classification()`, `parse_answer_plan()` |
| Renderer | `plan_renderer.py` | Composes header / performance / top_listings / listing_compare / channel_mix |
| Freeform LLM | `answer_planner.py` | When regex+intent router `PASS` on a confirmed thread, proposes a plan |
| Wiring | `thread_turn.py` | All thread turns execute via plan; tools accept `answer_plan=` |

**Blocks** (compose in order): `header`, `performance`, `top_listings`, `listing_compare`,
`channel_mix`, `none` (interpretation only).

**Basis**: `current` / `ly_pace` / `ly_final` — controls which columns/tables appear (e.g. LY-only
compare when the user says “last year”).

Existing TurnKinds still work: they map deterministically to plans (`plan_from_classification`).
`listing_compare` is one plan shape, not a one-off forever. New freeform asks can add shapes via
the planner without a new `TurnKind`.

### Path A (Claude tool loop)

`resolve_and_run_snapshot` accepts optional `answer_plan`. Claude is prompted to pass one that
matches the ask. If omitted, `plan_from_tool_args()` infers a plan from `fields`, `listing_name`,
and listings mentioned in the user message, then renders via `plan_renderer` (same path as thread
turns). Interaction events store `action.answer_plan` and logs include
`answer_plan path=... source=...`.

| Variable | Default | Purpose |
|----------|---------|---------|
| `ANSWER_PLANNER_ENABLED` | `true` | Freeform LLM plans on thread `PASS` |
| `ANSWER_PLANNER_MIN_CONFIDENCE` | `0.65` | Ignore low-confidence plans (fall through to Claude tool loop) |

## Interaction corpus (Phase 1.5)

Every `@mention` is logged locally (when `INTERACTION_LOGGING=true`, default on):

| Location | Contents |
|----------|----------|
| `data/.cache/interactions/YYYY-MM-DD.jsonl` | Append-only interaction events |
| `data/.cache/interactions/channels/{channel}.json` | Per-channel aggregates |

Each event records: message, route kind/source, query, outcome, latency, and top similar past turns (retrieval hook for Phase 1.6).

## Intent router (Phase 1.6)

When regex rules return `pass` but thread state exists, an LLM intent router runs with:

- Current thread state (`pending_query` / `last_query`)
- Channel usage profile
- Top similar past interactions from the corpus

It returns structured JSON (`confirm`, `follow_up`, `date_override`, etc.). If confidence is above threshold, Python executes the snapshot — no manual regex additions needed for new phrasing.

| Variable | Default | Purpose |
|----------|---------|---------|
| `INTENT_ROUTER_ENABLED` | `true` | Set `false` to use regex rules only |
| `INTENT_ROUTER_MIN_CONFIDENCE` | `0.65` | Minimum router confidence to act |

- `user_id` is stored as a salted hash, not plain text
- Set `INTERACTION_LOGGING=false` to disable
- Optional: `INTERACTION_USER_HASH_SALT` for user id hashing

## Run

```bash
./scripts/run_slack_bot.sh
PYTHONPATH=src python -m pytest tests/chat/ -v
```
