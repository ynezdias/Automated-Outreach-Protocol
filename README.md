# Automated-Outreach-Protocol

Automated outreach system built on AWS (CDK, Python) with a Salesforce integration.
Currently a repository skeleton — see [CLAUDE.md](CLAUDE.md) for conventions and
[docs/DECISIONS.md](docs/DECISIONS.md) for architecture decisions.

## Setup

```sh
uv sync                      # install Python 3.12 deps (requires uv)
uv run pre-commit install    # enable git hooks (ruff + detect-secrets)
```

## Development

```sh
make check    # lint (ruff), typecheck (mypy --strict), unit tests (pytest)
make fmt      # auto-fix lint and formatting
cdk synth     # synthesize the four CDK stacks (requires Node.js + npm i -g aws-cdk)
```

## Local classify service (rules-v1)

Rules-only reply classification over HTTP: deterministic guardrails +
`docs/TAXONOMY.md` pattern buckets. No trained model yet, never sends a
message, no Salesforce access. The pure handler is
`src/classifier/handler.py` (Lambda-bound, WO-24);
`research/serve_local.py` is the local FastAPI wrapper — interactive docs at
`/docs` (click Authorize, paste the token).

```sh
export CLASSIFY_API_TOKEN=pick-a-long-random-string   # PowerShell: $env:CLASSIFY_API_TOKEN = "..."
uv run uvicorn serve_local:app --app-dir research --port 8100
```

```sh
curl -s http://127.0.0.1:8100/v1/classify \
  -H "Authorization: Bearer $CLASSIFY_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"message_id": "7590443", "from_number": "+15512357742", "body": "Who is this?", "channel": "sms"}'
# {"action":"human_review","intent":"Question","rule":null,"trigger":null,
#  "handoff_reason":"handoff intent Question (ends with '?')","confidence":null,
#  "model_version":"rules-v1","latency_ms":0.18}
```

`GET /health` (same bearer token) returns the version and a SHA-256 over the
rule sources + handoff config, so a deployed instance can prove which rules it
is running.

## Layout

- `infra/` — AWS CDK app; stacks: data, pipeline, inference, observability
- `salesforce/` — SFDX project with scratch org definition
- `tests/` — pytest suite
- `docs/DECISIONS.md` — ADRs
- `.github/workflows/ci.yml` — CI: lint, typecheck, tests, cdk synth, Salesforce
  scratch org validation (requires the `SFDX_AUTH_URL` repo secret; self-skips if absent)
