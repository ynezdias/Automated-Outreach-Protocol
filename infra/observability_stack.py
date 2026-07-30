"""Observability stack: monitoring, logging, and alerting resources."""

from typing import Any

from aws_cdk import Duration, Stack
from aws_cdk import aws_cloudwatch as cloudwatch
from constructs import Construct

SYNC_METRIC_NAMESPACE = "Outreach/Sync"
DRIFT_ALARM_THRESHOLD_PERCENT = 1


class ObservabilityStack(Stack):
    """Alarms over the metrics the pipeline and sync Lambdas publish."""

    def __init__(self, scope: Construct, construct_id: str, **kwargs: Any) -> None:
        super().__init__(scope, construct_id, **kwargs)

        drift_metric = cloudwatch.Metric(
            namespace=SYNC_METRIC_NAMESPACE,
            metric_name="ReconciliationDriftPercent",
            statistic="Maximum",
            period=Duration.hours(24),
        )
        cloudwatch.Alarm(
            self,
            "SyncDriftAlarm",
            metric=drift_metric,
            threshold=DRIFT_ALARM_THRESHOLD_PERCENT,
            evaluation_periods=1,
            comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
            # Missing data means the reconciliation job stopped running — that is
            # itself an incident, so fail loud rather than quietly going green.
            treat_missing_data=cloudwatch.TreatMissingData.BREACHING,
            alarm_description=(
                "Lake vs Salesforce record-count drift exceeded "
                f"{DRIFT_ALARM_THRESHOLD_PERCENT}% (or reconciliation stopped reporting)."
            ),
        )
