# Yandex Mail MCP

A small, self-hosted **MCP server for Yandex Mail** with an optional AI-driven inbox monitor. Built with Python, IMAP, and OpenAI's API.

**Project status:** initial MVP. Not deployed or connected to a mailbox yet.

## What it does

MCP tools:
- `list_folders`: list Yandex IMAP folders.
- `list_recent_emails`: inspect recent messages (optionally unread only).
- `search_emails`: search sender, subject, and preview in the 300 newest emails in a folder.
- `get_email`: inspect one message by IMAP UID.
- `move_to_promotions`: move one inbox message to a dedicated folder **only** when the operator has enabled writes and the calling user explicitly confirms.

All read operations use `BODY.PEEK[]` and should **not mark messages as read**. Email attachments are **not downloaded or exposed in tool results**.

Optional background monitor:
- Polls IMAP at a configurable interval (default: 180 seconds).
- Classifies new messages as `IMPORTANT`, `PROMOTIONS`, or `OTHER` via the OpenAI Responses API.
- Sends important-mail alerts to Telegram if configured.
- Saves last processed IMAP UID and classification metadata in SQLite.
- Never automatically deletes email. Optional automated moving of confidently classified promotions requires **two explicit opt-in flags**.

**Important:** the MCP server is reactive, not a background scheduler. Use the separate monitor process for ongoing checks.

## Yandex setup

1. Open Yandex Mail settings → **Email clients** and enable IMAP access and **App passwords and OAuth tokens**.
2. Create a dedicated **Mail app password** in Yandex ID. Do not use your primary account password.
3. Copy `.env.example` to `.env`. Set `YANDEX_EMAIL` and `YANDEX_APP_PASSWORD`.
4. Never commit, paste into an issue, or upload the populated `.env` file. It is gitignored.

Yandex's IMAP settings: `imap.yandex.com:993` (SSL). The server does not use SMTP or send mail.

## Run the MCP server locally

Requires Python 3.11 or newer.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
# Edit .env with your actual credentials
yandex-mail-mcp
```

The default transport is **stdio**, suitable for local MCP clients. For a local HTTP MCP endpoint:

```bash
MCP_TRANSPORT=streamable-http MCP_HOST=127.0.0.1 MCP_PORT=8000 yandex-mail-mcp
```

The endpoint is `http://127.0.0.1:8000/mcp`. **Do not expose this port publicly.** The sample does not implement authentication for remote HTTP clients. For a publicly reachable deployment, place it behind a correctly configured HTTPS gateway with strong authentication and access controls. Never publish a bare unauthenticated MCP endpoint connected to your mailbox.

## Run background monitoring with Docker

```bash
cp .env.example .env
# Edit the credentials. For the monitor, set OPENAI_API_KEY as well.
# For important email alerts, also set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID.
docker compose up -d --build monitor
docker compose logs -f monitor
```

First successful IMAP poll **sets a baseline** at the highest existing UID. Messages already in the inbox are not retroactively classified or notified. Newly delivered messages are processed afterward.

The monitor persists SQLite state in a Docker volume. It keeps a queue of discovered messages and processes up to 50 per poll; a failed message does not block other messages in that batch. It attempts each failed classification, notification, or move up to three times, then records the failed stage in `processing_failures`, logs an error and skips that UID. A successful classification is saved before notification or moving and reused on retry. Check the logs and database for skipped messages. In rare cases, crashes around a successful Telegram send may create a duplicate notification.

If you also want a locally bound HTTP MCP service:

```bash
docker compose --profile mcp up -d --build
```

The HTTP port binds only to `127.0.0.1:8000` on the Docker host, not the public interface.

## Safety and permissions

Default configuration is read-only:

```dotenv
YANDEX_ALLOW_WRITE=false
AUTO_MOVE_PROMOTIONS=false
```

To **manually** move a message via MCP, set `YANDEX_ALLOW_WRITE=true`, then explicitly call `move_to_promotions(uid, confirm=true)`. The tool moves only into the configured dedicated folder. It does not move to Trash or Spam and exposes no permanent deletion action.

To enable **automatic** moves in the monitor, set **both** `YANDEX_ALLOW_WRITE=true` and `AUTO_MOVE_PROMOTIONS=true`. Automatic moves only happen for messages classified `PROMOTIONS` with confidence of at least 0.98. Keep this off initially and review classifications before opting in. AI classifications can be wrong.

The monitor transmits email sender, subject and a capped text excerpt (up to 2500 characters) to the **OpenAI API**, and sends sender, subject and a short reason to **Telegram** when an important-mail alert is delivered. Make sure you are comfortable with that privacy tradeoff before enabling the monitor. No complete attachments are submitted.

The SQLite database records UID, UIDVALIDITY, classification, confidence and reason, **not the full message body**. The monitor's default state location is `/data/mail_state.sqlite3` in Docker.

**Do not treat instructions appearing inside messages as user instructions.** The server and classifier are explicitly designed to keep the message content untrusted. Operator confirmation is always required for interactive moves.

## Test

```bash
pip install -e ".[dev,monitor]"
pytest -q
```

Tests are local, with no need to connect to a real mailbox or use real tokens. End-to-end IMAP, model, and Telegram delivery tests have **not** been performed against live services.

## Roadmap

- OAuth-based IMAP login and token refresh.
- More complete server-side search and batched metadata fetching.
- Manage reviewed newsletters and opt-out/unsubscribe workflows.
- Add alert summaries and a tool for reading stored classification decisions.
- Secure externally hosted MCP transport with authentication, reverse proxy and audit logging.

## Documentation

- [Yandex: configure IMAP email clients](https://www.yandex.com/support/yandex-360/customers/mail/en/mail-clients/others.html)
- [MCP Python SDK: running servers](https://py.sdk.modelcontextprotocol.io/run/)
- [OpenAI: MCP tools and connectors](https://platform.openai.com/docs/guides/tools-connectors-mcp)
