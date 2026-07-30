# PROVIDER.md — TextTorrent wire contract

> **STATUS: DOCS-DERIVED DRAFT. NOTHING BELOW IS CAPTURE-VERIFIED.**
>
> Source: the official reference at <https://texttorrent.com/docs/api>, fetched
> in full on 2026-07-30. The governing work order requires this file to be
> written from **captured** request/response evidence, because this repo has
> already shipped one guessed contract (ADR-016). Every section therefore
> carries a `Verified:` marker naming the fixture that must back it; run
> `research/texttorrent_capture.py` with real credentials to produce them.
> **Do not reconcile provider code against a section still marked NO.**

## 1. Base URL, authentication, response envelope

`Verified: NO — pending fixtures 01_auth_me.json, 02_unauthenticated.json`

- Base URL: `https://api.texttorrent.com/api/v1`
- Auth: two headers on every request — `X-API-SID: SID…` and
  `X-API-PUBLIC-KEY: PK…` (account settings → API dashboard). No OAuth, no
  request signing. Unauthenticated → `401`.
- Every response uses one envelope:

  ```json
  {"code": 200, "success": true, "message": "…", "data": {…}, "errors": null}
  ```

- Rate limit: **60 requests/minute** account-wide → `429` + `Retry-After`
  header. This budget is shared by sends AND any inbound polling.

Salesforce consequence (on reconciliation): a Named Credential with `Password`
protocol cannot inject these two headers — the credential must move to custom
headers (Named/External Credential custom headers, secrets still outside code).

## 2. Sending

`Verified: NO — pending fixtures 05_chat_create.json, 06_send.json, 08_bad_request.json`

Sending requires an existing **conversation** (`chat_id`); a first touch is
therefore two calls:

### 2.1 Start New Chat — `POST /inbox/chat/create` (JSON)

- Body: `{"receiver_number": "<10 digits, no +1>", "sender_id": "+1…"}`
  (sender must be an active account number).
- `201`: `data.id` is the **chat id**; also `contact_id`, `from_number`.
  Creates the contact automatically if new.
- `404` `"This contact is blacklisted."` — send must stop (their suppression).
- `404` `"You have already started a chat with this contact."` — recover the
  existing chat id via `GET /inbox?search=<number>`.

### 2.2 Send Message — `POST /inbox/chat` (**multipart/form-data**, not JSON)

- Fields: `message` (≤5000 chars), `chat_id`, `from_number`, `to_number`;
  optional `chatFile` for MMS.
- `201`: `data.id` (integer) is the **provider message id** →
  `Provider_Message_Id__c`; also `data.chat_id`, `data.direction`,
  `data.msg_type`, `data.status`.
- Known error text: `"Unable to send message. Sender number is not active!"`.
- Validation error shape (`422`) — capture pending.

> **⚠️ AI rewriting on the send path.** The docs state sends are
> *"automatically cleaned using AI to fix encoding issues"*. That is the
> Conflict-A rewriter, server-side, on the API path — the byte-identity canary
> (WO-10) is mandatory before any production send. The harness's canary body
> contains curly quotes / em dash / accents / emoji for exactly this test.
> Separately, `POST /inbox/generate/ai/response` (5.11 "Generate AI Replies")
> exists — **never call it**: no LLM anywhere in the reply path (anti-goal).

## 3. Inbound messages

`Verified: NO — pending fixtures 04_inbox_list.json, 07_poll.json`

**The API documents no webhooks.** The complete endpoint inventory (sections
1–10, every path enumerated) contains no webhook registration, no signature
scheme, no push notification config. The docs' own "Real-Time Updates" advice
is a **polling** example. There are no delivery callbacks either (§4).

Consequences, pending the inbound-transport decision (see ADR-017):

- The WO-08 Apex REST webhook endpoints have **no caller** for this vendor;
  Twilio-style `validateInbound` (HMAC-SHA1 + `X-Twilio-Signature` fallback)
  validates traffic that will never arrive. Removal is part of that decision.
- Open item: whether the account **UI** offers webhooks the API docs omit —
  check the dashboard before finalizing (work-order item 2 assumed they exist).

Polling contract:

- `GET /inbox` — paginated chat list: `chat_id`, contact fields, `number`,
  `last_message`, `last_chat_time` (UTC ISO), `unread_count`, `send_by`
  (`"contact"` = last message is theirs). Filters: `search`, `folder`, `time`,
  `unread`, `limit`, `page`.
- `GET /inbox/{chat_id}` — chat details + paginated `messages.data[]`:
  integer `id`, `chat_id`, `message`, `direction` (`inbound`/`outbound`),
  `status` (integer — semantics unverified), `from_number`, `to_number`,
  `media_url`, `created_at`, `updated_at`.
- **Gotcha:** fetching chat details *marks the conversation read*. Poller
  idempotency must key on message `id` vs stored `Provider_Message_Id__c`,
  never on unread state.

## 4. Delivery status

`Verified: NO — pending 07_poll.json status transitions`

No status callbacks exist. Delivery state is the integer `status` on message
objects (poll `GET /inbox/{chat_id}` or `GET /inbox/statistics/bulk`). The
value set and transition timing must come from poll captures.

## 5. Suppression surfaces (Conflict B integration points)

`Verified: NO`

- Opt-out words: `GET/POST/PUT /contact/opt-out-word`, bulk delete, export.
  Matching is case-insensitive on incoming messages and auto-blocks senders.
- Blocked list: `GET /contact/blocked-list`, add, `/remove`, export. Blocked
  contacts receive nothing (campaigns, automations, replies).
- Chat create rejects blacklisted contacts (§2.1) — their side of suppression.

## 6. Capture checklist

| Fixture (tests/fixtures/texttorrent/) | Verifies |
| --- | --- |
| `01_auth_me.json` | §1 auth headers + envelope |
| `02_unauthenticated.json` | §1 401 error shape |
| `03_active_numbers.json` | §2 sender inventory shape |
| `04_inbox_list.json` | §3 chat-list poll contract |
| `05_chat_create.json` | §2.1 create + error variants |
| `06_send.json` | §2.2 send response, message id location |
| `07_poll*.json` | §3 message shape, §4 status transitions, real inbound reply |
| `08_bad_request.json` | §2.2 422 validation shape |
| `09_not_found.json` | §1 404 shape |

Raw captures stay outside the repo (real numbers + message content; an editor
auto-commit has pushed PII once already). Committed fixtures are sanitized
copies: auth values redacted by the harness, phone numbers mapped to reserved
`+1555…` values, content otherwise byte-identical — with the mapping noted in
`tests/fixtures/texttorrent/README.md` when they land.
