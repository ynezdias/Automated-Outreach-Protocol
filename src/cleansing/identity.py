"""Deterministic contact identity: normalized key and UUIDv5 external ID.

The external ID is the Salesforce upsert key (Bulk API 2.0 external ID field),
so it must be stable across pipeline runs: the same normalized identifiers must
always produce the same UUID. Never change ``EXTERNAL_ID_NAMESPACE`` — doing so
re-keys every contact.
"""

import uuid

EXTERNAL_ID_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_DNS, "outreach.fundmatellc.com")


def normalized_key(phone: str | None, email: str | None) -> str:
    """Stable dedupe key over the normalized identifiers."""
    return f"{phone or ''}|{email or ''}"


def external_id(key: str) -> str:
    return str(uuid.uuid5(EXTERNAL_ID_NAMESPACE, key))
