"""Local-only HTTP wrapper around the pure rules classifier. NOT deployed.

WO-24 wraps the same ``classifier.handler.classify`` in Lambda + API Gateway;
this wrapper exists so the contract can be exercised interactively first —
open http://127.0.0.1:8100/docs and press Authorize.

This service classifies only: it never sends a message (no provider import
anywhere beneath it) and has no Salesforce access. Message bodies are never
logged — only their SHA-256. Any internal failure returns HTTP 200 with
``action=human_review`` (fail closed), never a 500.

Run:
    $env:CLASSIFY_API_TOKEN = "pick-a-long-random-string"    # required
    uv run uvicorn serve_local:app --app-dir research --port 8100
"""

import hashlib
import logging
import os
import secrets
import sys
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from classifier.handler import MODEL_VERSION, classify, fail_closed, rule_set_hash

logger = logging.getLogger("classify.local")
bearer_scheme = HTTPBearer(auto_error=False)


def require_token(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)] = None,
) -> None:
    expected = os.environ.get("CLASSIFY_API_TOKEN")
    if not expected:
        raise HTTPException(status_code=503, detail="CLASSIFY_API_TOKEN is not configured")
    if credentials is None or not secrets.compare_digest(credentials.credentials, expected):
        raise HTTPException(status_code=401, detail="missing or invalid bearer token")


app = FastAPI(
    title="Outreach reply classifier (rules-v1, local)",
    version=MODEL_VERSION,
    description=(
        "Rules-only classification: deterministic guardrails + TAXONOMY.md "
        "pattern buckets. No trained model, no message sending, no Salesforce. "
        "`action` is one of suppress_and_stop | human_review | auto_reply | "
        "no_action; rules-v1 never returns auto_reply (no approved templates "
        "exist yet). `confidence` is always null — rules, not probabilities."
    ),
)

_CLASSIFY_OPENAPI = {
    "requestBody": {
        "required": True,
        "content": {
            "application/json": {
                "schema": {
                    "type": "object",
                    "required": ["body"],
                    "properties": {
                        "message_id": {"type": "string"},
                        "from_number": {"type": "string"},
                        "body": {"type": "string"},
                        "channel": {"type": "string", "enum": ["sms", "email"]},
                        "thread": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "body": {"type": "string"},
                                    "direction": {"type": "string"},
                                    "is_auto_reply": {"type": "boolean"},
                                },
                            },
                        },
                    },
                },
                "example": {
                    "message_id": "7590443",
                    "from_number": "+15512357742",
                    "body": "Who is this?",
                    "channel": "sms",
                    "thread": [],
                },
            }
        },
    }
}


@app.get("/health", dependencies=[Depends(require_token)])
def health() -> dict[str, str]:
    return {"version": MODEL_VERSION, "rule_set_hash": rule_set_hash()}


@app.post("/v1/classify", dependencies=[Depends(require_token)], openapi_extra=_CLASSIFY_OPENAPI)
async def classify_route(request: Request) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    try:
        parsed = await request.json()
        if isinstance(parsed, dict):
            payload = parsed
    except Exception:
        logger.warning("unparseable request body; failing closed")
    try:
        result = classify(payload)
    except Exception:  # classify is fail-closed already; this is belt and braces
        logger.exception("classify raised; failing closed")
        result = fail_closed("internal_error:wrapper")
    body = payload.get("body")
    body_hash = hashlib.sha256(body.encode()).hexdigest() if isinstance(body, str) else None
    logger.info(
        "classified message_id=%s body_sha256=%s action=%s intent=%s rule=%s latency_ms=%s",
        payload.get("message_id"),
        body_hash,
        result["action"],
        result["intent"],
        result["rule"],
        result["latency_ms"],
    )
    return result
