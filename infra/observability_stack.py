"""Observability stack: monitoring, logging, and alerting resources."""

from typing import Any

from aws_cdk import Duration, Stack
from aws_cdk import aws_cloudwatch as cloudwatch
from aws_cdk import aws_cloudwatch_actions as cloudwatch_actions
from aws_cdk import aws_sns as sns
from aws_cdk import aws_sns_subscriptions as subscriptions
from constructs import Construct

SYNC_METRIC_NAMESPACE = "Outreach/Sync"
INBOUND_METRIC_NAMESPACE = "Outreach/Inbound"
TEXTTORRENT_METRIC_NAMESPACE = "Outreach/TextTorrent"
SUPPRESSION_METRIC_NAMESPACE = "Outreach/Suppression"
DRIFT_ALARM_THRESHOLD_PERCENT = 1
INBOUND_GAP_THRESHOLD_SECONDS = 4 * 60 * 60  # 4 business hours
PAGING_EMAIL = "ydias@fundmatellc.com"


class ObservabilityStack(Stack):
    """Alarms over the metrics the pipeline and sync Lambdas publish."""

    def __init__(self, scope: Construct, construct_id: str, **kwargs: Any) -> None:
        super().__init__(scope, construct_id, **kwargs)

        paging_topic = sns.Topic(self, "PagingTopic")
        paging_topic.add_subscription(subscriptions.EmailSubscription(PAGING_EMAIL))

        drift_metric = cloudwatch.Metric(
            namespace=SYNC_METRIC_NAMESPACE,
            metric_name="ReconciliationDriftPercent",
            statistic="Maximum",
            period=Duration.hours(24),
        )
        drift_alarm = cloudwatch.Alarm(
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
        drift_alarm.add_alarm_action(cloudwatch_actions.SnsAction(paging_topic))

        inbound_gap_metric = cloudwatch.Metric(
            namespace=INBOUND_METRIC_NAMESPACE,
            metric_name="InboundGapBusinessSeconds",
            statistic="Maximum",
            period=Duration.minutes(15),
        )
        inbound_gap_alarm = cloudwatch.Alarm(
            self,
            "InboundGapAlarm",
            metric=inbound_gap_metric,
            threshold=INBOUND_GAP_THRESHOLD_SECONDS,
            evaluation_periods=1,
            comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
            # Missing data = the detector itself died (or no inbound message has
            # ever been stored). Both must page, never quietly stay green: this
            # alarm exists to catch inbound transport failing silently.
            treat_missing_data=cloudwatch.TreatMissingData.BREACHING,
            alarm_description=(
                "No inbound message stored in Salesforce for >= 4 business hours "
                "(Mon-Fri send-window hours). Inbound transport may be silently "
                "failing — replies are being lost until this is resolved."
            ),
        )
        inbound_gap_alarm.add_alarm_action(cloudwatch_actions.SnsAction(paging_topic))

        canary_metric = cloudwatch.Metric(
            namespace=TEXTTORRENT_METRIC_NAMESPACE,
            metric_name="CanaryByteIdentical",
            statistic="Minimum",
            period=Duration.hours(24),
        )
        canary_alarm = cloudwatch.Alarm(
            self,
            "RewriteCanaryAlarm",
            metric=canary_metric,
            threshold=1,
            evaluation_periods=1,
            comparison_operator=cloudwatch.ComparisonOperator.LESS_THAN_THRESHOLD,
            # A missing datapoint means the canary didn't run — which is the same
            # incident as a mutated message: nobody verified byte identity today.
            treat_missing_data=cloudwatch.TreatMissingData.BREACHING,
            alarm_description=(
                "TextTorrent stored message text differs from the submitted bytes "
                "(AI rewriter active?) or the daily canary failed to run. Approved "
                "template immutability is not being verified."
            ),
        )
        canary_alarm.add_alarm_action(cloudwatch_actions.SnsAction(paging_topic))

        divergence_metric = cloudwatch.Metric(
            namespace=SUPPRESSION_METRIC_NAMESPACE,
            metric_name="OptOutDivergence",
            statistic="Maximum",
            period=Duration.hours(24),
        )
        divergence_alarm = cloudwatch.Alarm(
            self,
            "OptOutDivergenceAlarm",
            metric=divergence_metric,
            threshold=0,
            evaluation_periods=1,
            comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
            # Zero is the only acceptable number, and a missing datapoint means
            # the reconciliation job did not run — page either way.
            treat_missing_data=cloudwatch.TreatMissingData.BREACHING,
            alarm_description=(
                "Our suppression list and TextTorrent's blocked list diverged (an "
                "opt-out was missed on one side), or the nightly reconciliation "
                "did not run."
            ),
        )
        divergence_alarm.add_alarm_action(cloudwatch_actions.SnsAction(paging_topic))
