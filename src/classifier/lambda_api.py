"""Lambda Function URL adapter for the pure rules classifier.

Same contract and guarantees as ``research/serve_local.py``, minus the web
framework: bearer auth on every request — tokens come from Secrets Manager
(env ``CLASSIFY_TOKEN_SECRET_IDS`` names one or more secrets, comma-separated,
each holding one token), cached for the lifetime of the execution
environment — SHA-256-only logging, and fail-closed 200s: classification
never surfaces a 500.

Multiple tokens exist so each caller gets a revocable credential (owner vs
manager). Which token authenticated is logged BY SECRET NAME on every
request; token values never appear in logs.

This function classifies and returns JSON, nothing else. No Salesforce
access, no send capability — the deployment asset ships only this package
and ``guardrails`` (see infra/inference_stack.py), and the role can read
exactly the token secrets.
"""

import base64
import hashlib
import json
import logging
import os
import secrets
import time
from typing import Any

import boto3

from classifier.handler import MODEL_VERSION, classify, fail_closed, rule_set_hash

logger = logging.getLogger("classify.lambda")

TOKEN_SECRETS_ENV = "CLASSIFY_TOKEN_SECRET_IDS"
_token_cache: dict[str, str] | None = None


def _expected_tokens() -> dict[str, str]:
    """{secret name -> token} from Secrets Manager, fetched once per environment."""
    global _token_cache
    if _token_cache is None:
        secret_ids = [
            part.strip()
            for part in os.environ.get(TOKEN_SECRETS_ENV, "").split(",")
            if part.strip()
        ]
        if not secret_ids:
            return {}
        client = boto3.client("secretsmanager")
        _token_cache = {
            secret_id: str(client.get_secret_value(SecretId=secret_id)["SecretString"])
            for secret_id in secret_ids
        }
    return _token_cache


def _authenticated_secret(headers: dict[str, str]) -> str | None:
    """The NAME of the secret whose token matched, or None."""
    supplied = headers.get("authorization", "")
    if not supplied.startswith("Bearer "):
        return None
    for secret_id, token in _expected_tokens().items():
        if secrets.compare_digest(supplied[7:], token):
            return secret_id
    return None


def _json_response(status: int, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(payload),
    }


def _payload(event: dict[str, Any]) -> dict[str, Any]:
    raw = str(event.get("body") or "")
    if event.get("isBase64Encoded"):
        raw = base64.b64decode(raw).decode(errors="replace")
    try:
        parsed = json.loads(raw)
    except ValueError:
        logger.warning("unparseable request body; failing closed")
        return {}
    return parsed if isinstance(parsed, dict) else {}


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Route a Function URL event. Auth first; classification fails closed."""
    started = time.perf_counter()
    method = event.get("requestContext", {}).get("http", {}).get("method", "")
    path = event.get("rawPath", "")
    headers = {str(k).lower(): str(v) for k, v in (event.get("headers") or {}).items()}

    try:
        if not _expected_tokens():
            return _json_response(503, {"detail": f"{TOKEN_SECRETS_ENV} is not configured"})
        token_name = _authenticated_secret(headers)
    except Exception:
        logger.exception("token secrets unavailable; refusing all requests")
        return _json_response(503, {"detail": "auth tokens unavailable"})
    if token_name is None:
        return _json_response(401, {"detail": "missing or invalid bearer token"})
    logger.info("authenticated token=%s method=%s path=%s", token_name, method, path)

    if method == "GET" and path == "/health":
        return _json_response(200, {"version": MODEL_VERSION, "rule_set_hash": rule_set_hash()})
    if method == "POST" and path == "/v1/classify":
        payload = _payload(event)
        try:
            result = classify(payload)
        except Exception:  # classify is fail-closed already; belt and braces
            logger.exception("classify raised; failing closed")
            result = fail_closed("internal_error:wrapper", started)
        body = payload.get("body")
        body_hash = hashlib.sha256(body.encode()).hexdigest() if isinstance(body, str) else None
        logger.info(
            "classified token=%s message_id=%s body_sha256=%s action=%s intent=%s rule=%s "
            "latency_ms=%s",
            token_name,
            payload.get("message_id"),
            body_hash,
            result["action"],
            result["intent"],
            result["rule"],
            result["latency_ms"],
        )
        return _json_response(200, result)
    return _json_response(404, {"detail": "not found"})
