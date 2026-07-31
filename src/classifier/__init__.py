"""Reply classification. rules-v1: guardrails + TAXONOMY.md pattern buckets.

No trained model exists yet; when it does (WO-24+), it slots in behind the
same ``classify()`` contract. Nothing in this package sends messages or
touches Salesforce — it reads a reply and returns a routing decision.
"""

from classifier.handler import ACTIONS, MODEL_VERSION, classify, rule_set_hash

__all__ = ["ACTIONS", "MODEL_VERSION", "classify", "rule_set_hash"]
