"""Unit tests for the observability stack."""

import aws_cdk as cdk
import pytest
from aws_cdk.assertions import Match, Template

from infra.observability_stack import ObservabilityStack


@pytest.fixture(scope="module")
def template() -> Template:
    return Template.from_stack(ObservabilityStack(cdk.App(), "TestObservabilityStack"))


def test_drift_alarm_over_one_percent(template: Template) -> None:
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


def test_rewrite_canary_alarm_on_any_non_identical_day(template: Template) -> None:
    template.has_resource_properties(
        "AWS::CloudWatch::Alarm",
        Match.object_like(
            {
                "Namespace": "Outreach/TextTorrent",
                "MetricName": "CanaryByteIdentical",
                "Threshold": 1,
                "ComparisonOperator": "LessThanThreshold",
                "TreatMissingData": "breaching",
            }
        ),
    )


def test_optout_divergence_alarm_is_zero_tolerance(template: Template) -> None:
    template.has_resource_properties(
        "AWS::CloudWatch::Alarm",
        Match.object_like(
            {
                "Namespace": "Outreach/Suppression",
                "MetricName": "OptOutDivergence",
                "Threshold": 0,
                "ComparisonOperator": "GreaterThanThreshold",
                "TreatMissingData": "breaching",
            }
        ),
    )


def test_inbound_gap_alarm_at_four_business_hours(template: Template) -> None:
    template.resource_count_is("AWS::CloudWatch::Alarm", 4)
    template.has_resource_properties(
        "AWS::CloudWatch::Alarm",
        Match.object_like(
            {
                "Namespace": "Outreach/Inbound",
                "MetricName": "InboundGapBusinessSeconds",
                "Threshold": 14400,
                "ComparisonOperator": "GreaterThanOrEqualToThreshold",
                # Missing data must breach: a dead detector or a never-populated
                # inbox both mean nobody is watching the inbound path.
                "TreatMissingData": "breaching",
            }
        ),
    )


def test_both_alarms_page_the_sns_topic(template: Template) -> None:
    template.resource_count_is("AWS::SNS::Topic", 1)
    template.has_resource_properties(
        "AWS::SNS::Subscription",
        Match.object_like({"Protocol": "email", "Endpoint": "ydias@fundmatellc.com"}),
    )
    for alarm in template.find_resources("AWS::CloudWatch::Alarm").values():
        assert alarm["Properties"]["AlarmActions"], "alarm has no paging action"
