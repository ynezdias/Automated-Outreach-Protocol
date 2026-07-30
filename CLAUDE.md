# CLAUDE.md — Outreach & Reply System

Read this before every task. It contains settled decisions. Do not re-litigate them; if you think one is wrong, say so and stop — do not silently build the alternative.

---

## What this is

An automated B2B outreach system. We send a first-touch message to prospective companies (financing offer), a classical NLP classifier reads their reply and predicts an intent, and a rules layer selects a pre-approved response template. Salesforce is the CRM, the send endpoint, and the system of record. AWS is the data lake and the inference layer.

---

## Settled architecture decisions

1. **Salesforce owns live state.** Lead record, conversation thread, status, review queue. There is no second live-state store.
2. **AWS owns the lake and the model.** S3, cleansing pipeline, training, and a stateless classification API.
3. **The reply engine is a discriminative classifier**, not a generative model. TF-IDF (word 1–2 grams + `char_wb` 3–5 grams) → calibrated logistic regression. Every outbound message comes from a pre-approved, versioned template.
4. **Deterministic guardrails run before the model**, never after, and their output is never probabilistic.
5. **Failure mode is always "a human looks at it."** Any error, timeout, low confidence, or ambiguity routes to the human review queue. Never auto-send on uncertainty.
6. **Salesforce → AWS classification is a synchronous callout** via Named Credential with AWS SigV4 auth. (Async via Platform Events + EventBridge is a later option, not v1.)
7. **AWS → Salesforce lead sync is Bulk API 2.0 upsert** on an external ID, authenticated with the OAuth 2.0 JWT Bearer Flow.

---

## Hard invariants — these are compliance controls, not features

Violating any of these is a release blocker. Each needs a test that fails loudly.

- **Opt-out is regex, never ML.** `STOP`, `STOPALL`, `UNSUBSCRIBE`, `CANCEL`, `END`, `QUIT`, `REVOKE`, `OPTOUT`, `OPT OUT`, `REMOVE ME` — case-insensitive, punctuation-stripped, edit-distance-1 fuzzy. Matches suppress immediately and propagate to the suppression list within 60 seconds.
- **One auto-reply per inbound message.** Never two. Enforce at the send layer, not just the flow logic.
- **Loop breaker.** After 2 auto-replies in a thread, force human takeover.
- **Legal/escalation keywords never auto-reply.** `attorney`, `lawyer`, `lawsuit`, `sue`, `TCPA`, `FCC`, `complaint`, `harass`, `report you` → human queue, flagged.
- **Quiet hours enforced before send**, 8am–9pm in the recipient's local timezone, checked at the send layer.
- **Suppression check immediately before every send**, not just at list-build time.
- **Only `Approved` template versions are sendable.** Editing an approved template creates a new version; it never mutates in place.
- **Every outbound message is reconstructable**: template version, approver, predicted intent, confidence, model version, consent basis — all persisted.
- **Webhook handlers are idempotent** on the provider's message ID. Providers retry; double-logging causes double-replies.

---

## Anti-goals — do not build these

- **No LLM or generative model anywhere in the reply path.** Not for classification, not for drafting, not "just as a fallback." This is a compliance requirement.
- **No DynamoDB.** An earlier proposal included it as a status store. Salesforce holds state now. Adding it creates drift.
- **No fail-open behavior.** If the classifier API errors or times out, the message goes to a human. It does not get a default template.
- **No `Modify All Data`** on the integration user. Scoped Profile + Permission Set only.
- **No secrets in code or in Salesforce Custom Settings.** AWS Secrets Manager and Named Credentials only.
- **No monolithic Lambdas.** Each pipeline step is independently invocable and rerunnable.
- **No random train/test split.** Time-based only (see below).
- **No auto-promotion of models to production.** A human approves every promotion.

---

## Stack and conventions

**AWS:** Python 3.12, AWS CDK (Python) for all infrastructure. No console clicks — if it isn't in CDK, it doesn't exist. `uv` for dependency management, `ruff` for lint+format, `mypy --strict` on `src/`, `pytest` with `moto` for AWS mocking.

**ML:** scikit-learn, joblib for artifacts, no notebooks in the repo (convert exploration to scripts under `research/`).

**Salesforce:** source-tracked SFDX metadata, `sf` CLI, scratch orgs for development. Apex with 85%+ coverage on new classes. Flows for orchestration, Apex only for callouts and logic Flow can't express.

**Testing:** every guardrail rule gets a test with adversarial cases (typos, unicode lookalikes, embedded keywords, leading/trailing punctuation). Integration tests for both API contracts using recorded fixtures.

---

## Repo layout

```
infra/                 # CDK app — one stack per concern
  data_stack.py        # S3 zones, KMS, Glue catalog
  pipeline_stack.py    # Step Functions, cleansing Lambdas
  inference_stack.py   # API Gateway, classifier Lambda, ECR
  observability_stack.py
src/
  cleansing/           # normalize, validate, dedupe, suppress
  suppression/         # opt-out list read/write — compliance-critical
  guardrails/          # deterministic pre-classifier rules
  classifier/          # feature pipeline, training, inference handler
  salesforce/          # JWT auth, Bulk API 2.0 client, REST client
tests/
research/              # exploration scripts, not deployed
salesforce/
  force-app/main/default/{objects,classes,flows,permissionsets,layouts}
docs/
  DECISIONS.md         # append an ADR for any decision not covered here
```

---

## Model evaluation rules

- **Time-based split only.** Train on months 1–N, test on month N+1. Random splits leak near-duplicate messages across the boundary and overstate accuracy by 5–10 points.
- **Report per-class precision and recall, plus a confusion matrix.** Never report bare accuracy — the class imbalance makes it meaningless.
- **The operating metric is auto-reply precision at coverage X%.** That's what goes in the summary.
- **Calibrate** with `CalibratedClassifierCV(method='sigmoid')` on a held-out split. Uncalibrated probabilities make the confidence thresholds meaningless.
- **Per-intent thresholds**, tuned for precision ≥ 0.97 on the auto-reply decision.
- **Margin check:** if `top_confidence - runner_up_confidence < 0.15`, route to human regardless of absolute confidence.

---

## How to work

- **Ask before assuming** on anything touching consent, sending, or money. A wrong guess here has legal consequences, not just a bug.
- Work one work order at a time. Don't start the next one.
- Write the test before the implementation for anything in the invariants list.
- Small commits with conventional-commit messages. Don't refactor unrelated code.
- If a task requires a decision not covered here, stop, state the options and your recommendation, and wait.
- Append to `docs/DECISIONS.md` whenever you make a non-obvious choice.
- When a work order is complete, run the self-review checklist at the end of `WORK_ORDERS.md` before declaring done.
