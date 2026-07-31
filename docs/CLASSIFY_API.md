# CLASSIFY_API.md — rules-v1 classify service, Salesforce integration handoff

## Scope — read this first

- **This service classifies inbound message text and returns a verdict.
  That is all it does.**
- It does **not** send messages, receive messages, or perform the handoff.
- **Acting on the verdict is Salesforce's job, and that integration is not
  built yet.** Nothing happens to a lead, thread, or queue because this API
  returned something — until the Salesforce side exists and calls it.
- **Auth is a bearer token for now, but SigV4 via Named Credential is the
  settled Salesforce→AWS architecture** (CLAUDE.md; ADR-023 deliberately
  left the migration open). **Do not write production Apex against bearer
  auth until that decision is made.** The Apex sample below is the shape of
  the callout, not a green light on the auth scheme.

## Start here (no project context required)

This service reads one inbound SMS reply and returns a routing decision as
JSON: suppress the thread (opt-out), hand it to a human, or do nothing. It
is deterministic rules — compliance guardrails plus keyword intent buckets,
no ML model yet — and it can only *classify*: it cannot send messages and
has no access to the CRM. Any failure inside it returns "give it to a
human", never an error page.

- **URL:** `https://ypwmkzwk6oherd3ej4nsafvibq0ghibe.lambda-url.us-east-2.on.aws`
- **Getting a token:** every request needs `Authorization: Bearer <token>`.
  Tokens are personal and shared out of band by the project owner
  (ydias@fundmatellc.com) — yours is the *manager* token, revocable without
  affecting anyone else. Tokens are never in this repo or this document.
- **Try it** (Windows PowerShell, paste the whole block after inserting your
  token):

```powershell
$token = "<PASTE-YOUR-TOKEN-HERE>".Trim()
if ($token.Length -ne 48) { throw "Token is $($token.Length) chars, expected 48 - re-copy it as ONE line, no spaces." }
$base = "https://ypwmkzwk6oherd3ej4nsafvibq0ghibe.lambda-url.us-east-2.on.aws"
'{"message_id": "demo-1", "body": "yes im interested", "channel": "sms"}' | Out-File -Encoding ascii body.json
"--- health (version + rule hash):"
curl.exe -s -H "Authorization: Bearer $token" "$base/health"
""
"--- classify:"
curl.exe -s -H "Authorization: Bearer $token" -H "Content-Type: application/json" -d "@body.json" "$base/v1/classify"
```

Expected: `/health` prints the version and a rule-set hash (a fingerprint of
the exact rules running), and the classify call prints an 8-field JSON
response with `"action": "human_review"` and `"intent": "Interested"`.

**If you get a 401:** the token did not arrive intact or is not current.
The `.Trim()` + length check above catch pastes with stray whitespace or a
line break (terminals often hard-wrap long values on copy); if the length
check passes and you still get 401, your token value is stale — ask the
project owner to re-share it. This block was dry-run verified exactly as
written on 2026-07-31 against the live URL.

Everything below is the full contract.

---

Documentation for the deployed reply-classification API (ADR-023). The service
classifies one inbound reply and returns a routing decision. It never sends
messages, has no Salesforce access, and its deployed package contains no send
path. **Salesforce is not wired to this yet** — this document is the contract
for that work.

- **Base URL (dev account, us-east-2):**
  `https://ypwmkzwk6oherd3ej4nsafvibq0ghibe.lambda-url.us-east-2.on.aws`
  (CloudFormation output `OutreachInferenceStack.ClassifyUrl`; re-read it if
  the stack is ever recreated.)
- **Auth:** `Authorization: Bearer <token>` on **every** request, `/health`
  included. Token values are **not in this document**. Two independently
  revocable tokens exist in AWS Secrets Manager (us-east-2), shared out of
  band: `outreach/classify/token` (owner) and
  `outreach/classify/token-manager` (manager). Either authenticates; the
  service logs which one was used by secret NAME on every request, never by
  value — so each caller's traffic is distinguishable and one credential can
  be revoked (delete/rotate its secret, next cold start applies it) without
  breaking the other. In Salesforce the token belongs inside a Named/External
  Credential — never in code, Custom Settings, or Custom Metadata (CLAUDE.md
  anti-goal).
- **Transport note:** today's auth is bearer over a Lambda Function URL
  (ADR-023). The architecture's settled Salesforce→AWS pattern is a SigV4
  Named Credential against API Gateway; decide which one production wiring
  uses *before* building it — both fit a Named Credential, and ADR-023
  explicitly leaves the migration open.

## POST /v1/classify

Request (`Content-Type: application/json`):

```json
{
  "message_id": "7590443",
  "from_number": "+15512357742",
  "body": "yes im interested",
  "channel": "sms",
  "thread": [
    {"body": "opener text", "direction": "outbound", "is_auto_reply": true},
    {"body": "earlier reply", "direction": "inbound", "is_auto_reply": false}
  ]
}
```

| Field | Required | Notes |
| --- | --- | --- |
| `body` | **yes** | The inbound message text. Missing/non-string fails closed to `human_review`. |
| `message_id` | no | Provider message id; echoed into service logs (body itself is never logged — SHA-256 only). |
| `from_number` | no | Informational. |
| `channel` | no | `sms` (default) or `email`. Anything else fails closed. |
| `thread` | no | Prior messages, oldest first. Powers the loop breaker: 2+ outbound `is_auto_reply` messages force `human_review`. Send it once threads exist. |

Response — **exactly these 8 keys, always**:

```json
{
  "action": "human_review",
  "intent": "Interested",
  "rule": null,
  "trigger": null,
  "handoff_reason": "handoff intent Interested (contains 'interested' with no decline phrasing)",
  "confidence": null,
  "model_version": "rules-v1",
  "latency_ms": 0.101
}
```

| Key | Meaning |
| --- | --- |
| `action` | The routing decision. Route on THIS field only (table below). |
| `intent` | Taxonomy label (table below), or `null` on a fail-closed response. Informational — persist it, do not route on it. |
| `rule` | Guardrail rule that fired (`opt_out`, `legal_escalation`, `hostility`, `bounce_or_autoreply`, `loop_breaker`) or `null`. |
| `trigger` | The matched text that fired the rule, or `null`. |
| `handoff_reason` | Why a human gets it (guardrail rule, handoff intent, or `internal_error:*`), or `null`. Persist on the record. |
| `confidence` | **Always `null` in rules-v1.** These are rules, not probabilities. Do NOT build a threshold on this field; it gets values only when a trained model ships. |
| `model_version` | `"rules-v1"`. Persist per message — required for audit reconstructability (CLAUDE.md invariant). |
| `latency_ms` | Server-side compute time. Informational. |

HTTP statuses: `200` for every classification — including internal failures,
which return `action=human_review` (fail closed, never a 500). `401` missing
or wrong token. `503` the service cannot reach its own token secret. `404`
wrong path. **Treat any non-200 exactly like `human_review`.**

## What Salesforce does with each `action`

| `action` | Salesforce behavior |
| --- | --- |
| `suppress_and_stop` | Set `Opted_Out__c`, `DoNotCall`, `HasOptedOutOfEmail`, `Opt_Out_At__c`, `Opt_Out_Source__c`; `Outreach_Status__c = 'Suppressed'`; mirror to the AWS suppression list (existing `OutreachSuppressionSyncQueueable` path). **Halt the thread. Never reply — not even a confirmation.** Note: the Apex guardrails (`OutreachGuardrails`, ADR-020) run *before* any classify callout and are the authoritative opt-out control (opt-out is regex, never ML). This response is the same deterministic rule echoed back — a cross-check, not the primary control. |
| `human_review` | `Needs_Review__c = true`, assign to the reviewer queue, persist `handoff_reason` (and `rule`/`trigger` when present) on the `Outreach_Message__c` record. No automated response of any kind. |
| `no_action` | Log the classification on the record (`intent`, `model_version`). Nothing else happens. |
| `auto_reply` | **Unreachable in rules-v1** — no approved templates exist, so the service never returns it. Do not build handling for it. Defensively: if it (or any unknown value) ever appears, treat it as `human_review` until an approved-template flow exists. |

## `intent` values (13 + null)

`Interested`, `Amount_Given`, `Question`, `Request_More_Info`, `Call_Request`,
`Process_Update`, `Not_Interested`, `Wrong_Person`, `Hostile`, `Opt_Out`,
`Legal_Escalation`, `Auto_Reply`, `Unclear` — the ADR-022 taxonomy, matching
the `Outreach_Intent` Global Value Set. `null` appears only on fail-closed
responses. Which intents produce a `human_review` handoff is server
configuration (`CLASSIFY_HANDOFF_INTENTS` env var; currently `Interested`,
`Call_Request`, `Amount_Given`, `Question`, `Process_Update`) — Salesforce
should route on `action` and treat `intent` as data to store.

## Apex sample (fail-closed callout)

Assumes a Named Credential `Classify_API` pointing at the base URL whose
external credential injects the `Authorization: Bearer` header from its
stored secret.

```apex
public with sharing class ReplyClassifier {
    public class Classification {
        public String action;
        public String intent;
        public String rule;
        public String handoffReason;
        public String modelVersion;
    }

    /** Never throws. Any exception, timeout, or non-200 -> human_review. */
    public static Classification classify(String messageBody, String fromNumber, String messageId) {
        try {
            HttpRequest request = new HttpRequest();
            request.setEndpoint('callout:Classify_API/v1/classify');
            request.setMethod('POST');
            request.setHeader('Content-Type', 'application/json');
            request.setTimeout(10000);
            request.setBody(
                JSON.serialize(
                    new Map<String, Object>{
                        'message_id' => messageId,
                        'from_number' => fromNumber,
                        'body' => messageBody,
                        'channel' => 'sms'
                    }
                )
            );
            HttpResponse response = new Http().send(request);
            if (response.getStatusCode() != 200) {
                return failClosed('http_' + response.getStatusCode());
            }
            Map<String, Object> parsed = (Map<String, Object>) JSON.deserializeUntyped(
                response.getBody()
            );
            Classification result = new Classification();
            result.action = (String) parsed.get('action');
            result.intent = (String) parsed.get('intent');
            result.rule = (String) parsed.get('rule');
            result.handoffReason = (String) parsed.get('handoff_reason');
            result.modelVersion = (String) parsed.get('model_version');
            if (result.action == null) {
                return failClosed('malformed_response');
            }
            return result;
        } catch (Exception error) {
            return failClosed(error.getTypeName());
        }
    }

    private static Classification failClosed(String reason) {
        Classification fallback = new Classification();
        fallback.action = 'human_review';
        fallback.handoffReason = 'callout_failed:' + reason;
        fallback.modelVersion = 'rules-v1';
        return fallback;
    }
}
```

Standard callout rules apply: invoke before uncommitted DML in the
transaction, or from a Queueable — same pattern as the existing suppression
mirror.

## Operational notes

- **Timeout:** set 10 s. Observed compute is <1 ms; a cold start adds ~1 s
  (no provisioned concurrency at zero traffic — deliberate). On timeout or
  5xx: retry once, then fail closed to `human_review`. One call per inbound
  message.
- **Rate limits:** no gateway throttle in front of the Function URL; the
  Lambda scales to account concurrency. At planned volumes (tens of
  thousands of messages per month) this is nowhere near a limit. The
  service is stateless — retries are always safe.
- **`GET /health`** (same bearer auth) returns
  `{"version": "rules-v1", "rule_set_hash": "..."}`. The hash is a SHA-256
  over the guardrail rule sources, the intent buckets, the handler, and the
  effective handoff configuration: **two instances with the same hash run
  identical rules.** That is how staging and prod are told apart, and how a
  config drift shows up. Current dev deployment:
  `d23b5507a6fccabd5e1054d3f53ca845d48d3f829422412a9401f4f58856979a` <!-- pragma: allowlist secret -->
  (changes on any rules or handoff-config change — compare, don't pin).
- **Privacy:** message bodies never appear in service logs — only their
  SHA-256, the message id, and the resulting action/intent/rule.
