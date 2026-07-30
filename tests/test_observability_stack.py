"""Unit tests for the observability stack."""

import aws_cdk as cdk
from aws_cdk.assertions import Match, Template

from infra.observability_stack import ObservabilityStack


def test_drift_alarm_over_one_percent() -> None:
    template = Template.from_stack(ObservabilityStack(cdk.App(), "TestObservabilityStack"))
    template.resource_count_is("AWS::CloudWatch::Alarm", 1)
    template.has_resource_properties(
        "AWS::CloudWatch::Alarm",
        Match.object_like(
            {
                "Namespace": "Outreach/Sync",
                "MetricName": "ReconciliationDriftPercent",
                "Threshold": 1,
                "ComparisonOperator": "GreaterThanThreshold",
                "TreatMissingData": "breaching",
            }
        ),
    )
