"""Unit tests for the data stack's compliance-critical properties."""

from collections.abc import Mapping
from typing import Any

import aws_cdk as cdk
import pytest
from aws_cdk.assertions import Match, Template

from infra.data_stack import DataStack


@pytest.fixture(scope="module")
def template() -> Template:
    app = cdk.App()
    return Template.from_stack(DataStack(app, "TestDataStack"))


def _statements(policy: Mapping[str, Any]) -> list[dict[str, Any]]:
    statements: list[dict[str, Any]] = policy["Properties"]["PolicyDocument"]["Statement"]
    return statements


def _denies_insecure_transport(statement: dict[str, Any]) -> bool:
    return (
        statement.get("Effect") == "Deny"
        and statement.get("Condition", {}).get("Bool", {}).get("aws:SecureTransport") == "false"
    )


def test_audit_bucket_object_lock_compliance_mode(template: Template) -> None:
    """The audit bucket has Object Lock in compliance mode, 7-year default retention."""
    template.has_resource_properties(
        "AWS::S3::Bucket",
        Match.object_like(
            {
                "ObjectLockEnabled": True,
                "ObjectLockConfiguration": {
                    "ObjectLockEnabled": "Enabled",
                    "Rule": {"DefaultRetention": {"Mode": "COMPLIANCE", "Days": 2555}},
                },
            }
        ),
    )


def test_every_bucket_policy_denies_non_tls_requests(template: Template) -> None:
    """Both bucket policies deny requests where aws:SecureTransport is false."""
    policies = template.find_resources("AWS::S3::BucketPolicy")
    assert len(policies) == 2
    for policy in policies.values():
        assert any(_denies_insecure_transport(s) for s in _statements(policy))


def test_both_buckets_versioned_kms_encrypted_and_private(template: Template) -> None:
    buckets = template.find_resources("AWS::S3::Bucket")
    assert len(buckets) == 2
    for bucket in buckets.values():
        props = bucket["Properties"]
        assert props["VersioningConfiguration"] == {"Status": "Enabled"}
        sse = props["BucketEncryption"]["ServerSideEncryptionConfiguration"][0]
        assert sse["ServerSideEncryptionByDefault"]["SSEAlgorithm"] == "aws:kms"
        assert props["PublicAccessBlockConfiguration"] == {
            "BlockPublicAcls": True,
            "BlockPublicPolicy": True,
            "IgnorePublicAcls": True,
            "RestrictPublicBuckets": True,
        }


def test_separate_customer_managed_keys(template: Template) -> None:
    keys = template.find_resources("AWS::KMS::Key")
    assert len(keys) == 2
    for key in keys.values():
        assert key["Properties"]["EnableKeyRotation"] is True


def test_lifecycle_transitions(template: Template) -> None:
    template.has_resource_properties(
        "AWS::S3::Bucket",
        Match.object_like(
            {
                "LifecycleConfiguration": {
                    "Rules": Match.array_with(
                        [
                            Match.object_like(
                                {
                                    "Prefix": "raw/",
                                    "Transitions": [
                                        {
                                            "StorageClass": "INTELLIGENT_TIERING",
                                            "TransitionInDays": 30,
                                        }
                                    ],
                                }
                            ),
                            Match.object_like(
                                {
                                    "Prefix": "conversations/",
                                    "Transitions": [
                                        {"StorageClass": "GLACIER_IR", "TransitionInDays": 180}
                                    ],
                                }
                            ),
                        ]
                    )
                }
            }
        ),
    )


def test_audit_bucket_has_no_expiration_lifecycle(template: Template) -> None:
    """No lifecycle rule anywhere sets an expiration; audit bucket has none at all."""
    for bucket in template.find_resources("AWS::S3::Bucket").values():
        rules = bucket["Properties"].get("LifecycleConfiguration", {}).get("Rules", [])
        if bucket["Properties"].get("ObjectLockEnabled"):
            assert rules == []
        for rule in rules:
            assert "ExpirationInDays" not in rule
            assert "ExpirationDate" not in rule


def test_glue_database_and_three_crawlers(template: Template) -> None:
    template.resource_count_is("AWS::Glue::Database", 1)
    crawlers = template.find_resources("AWS::Glue::Crawler")
    paths = set()
    for crawler in crawlers.values():
        for target in crawler["Properties"]["Targets"]["S3Targets"]:
            path = target["Path"]
            assert isinstance(path, dict)  # Fn::Join over the bucket ref
            paths.add(path["Fn::Join"][1][-1])
    assert paths == {"/staging/", "/conversations/", "/training/"}


def test_s3_gateway_endpoint(template: Template) -> None:
    template.has_resource_properties(
        "AWS::EC2::VPCEndpoint",
        Match.object_like({"VpcEndpointType": "Gateway"}),
    )
