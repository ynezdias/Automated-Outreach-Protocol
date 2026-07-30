"""Data stack: S3 zones, KMS keys, Glue catalog, and S3 VPC gateway endpoint.

Zone layout (see CLAUDE.md):

    raw/            landing zone for unprocessed inbound data
    staging/        cleansed and normalized data
    suppression/    opt-out list — compliance-critical
    conversations/  message threads
    training/       training datasets
    models/         model artifacts
    audit/          immutable audit trail

The first six zones are prefixes on the data bucket. The ``audit/`` zone lives in
a dedicated bucket because S3 Object Lock is a bucket-level, creation-time
property — it cannot be scoped to a prefix, and compliance-mode-locking the data
bucket would make every zone immutable (ADR-008). Audit objects are still written
under an ``audit/`` key prefix so the logical layout is uniform.
"""

from typing import Any

from aws_cdk import CfnOutput, Duration, RemovalPolicy, Stack
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_glue as glue
from aws_cdk import aws_iam as iam
from aws_cdk import aws_kms as kms
from aws_cdk import aws_s3 as s3
from constructs import Construct

#: Zones stored as prefixes on the data bucket.
DATA_ZONES = ("raw", "staging", "suppression", "conversations", "training", "models")

#: Zones crawled into the Glue Data Catalog.
CRAWLED_ZONES = ("staging", "conversations", "training")

GLUE_DATABASE_NAME = "outreach"

#: Compliance-mode Object Lock default retention for audit records: 7 years.
AUDIT_RETENTION = Duration.days(7 * 365)


class DataStack(Stack):
    """S3 data lake, KMS encryption, Glue catalog, and S3 gateway endpoint."""

    def __init__(self, scope: Construct, construct_id: str, **kwargs: Any) -> None:
        super().__init__(scope, construct_id, **kwargs)

        data_key = kms.Key(
            self,
            "DataKey",
            alias="outreach/data",
            description="CMK for the outreach data lake bucket",
            enable_key_rotation=True,
            removal_policy=RemovalPolicy.RETAIN,
        )
        audit_key = kms.Key(
            self,
            "AuditKey",
            alias="outreach/audit",
            description="CMK for the outreach audit bucket",
            enable_key_rotation=True,
            removal_policy=RemovalPolicy.RETAIN,
        )

        self.data_bucket = s3.Bucket(
            self,
            "DataBucket",
            encryption=s3.BucketEncryption.KMS,
            encryption_key=data_key,
            bucket_key_enabled=True,
            versioned=True,
            event_bridge_enabled=True,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            removal_policy=RemovalPolicy.RETAIN,
            lifecycle_rules=[
                s3.LifecycleRule(
                    id="raw-to-intelligent-tiering",
                    prefix="raw/",
                    transitions=[
                        s3.Transition(
                            storage_class=s3.StorageClass.INTELLIGENT_TIERING,
                            transition_after=Duration.days(30),
                        )
                    ],
                ),
                s3.LifecycleRule(
                    id="conversations-to-glacier-ir",
                    prefix="conversations/",
                    transitions=[
                        s3.Transition(
                            storage_class=s3.StorageClass.GLACIER_INSTANT_RETRIEVAL,
                            transition_after=Duration.days(180),
                        )
                    ],
                ),
            ],
        )

        # Dedicated audit bucket (ADR-008): Object Lock in compliance mode with a
        # 7-year default retention. No lifecycle rules — audit records never expire.
        self.audit_bucket = s3.Bucket(
            self,
            "AuditBucket",
            encryption=s3.BucketEncryption.KMS,
            encryption_key=audit_key,
            bucket_key_enabled=True,
            versioned=True,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            object_lock_enabled=True,
            object_lock_default_retention=s3.ObjectLockRetention.compliance(AUDIT_RETENTION),
            removal_policy=RemovalPolicy.RETAIN,
        )

        database = glue.CfnDatabase(
            self,
            "CatalogDatabase",
            catalog_id=self.account,
            database_input=glue.CfnDatabase.DatabaseInputProperty(
                name=GLUE_DATABASE_NAME,
                description="Outreach data lake catalog",
            ),
        )

        crawler_role = iam.Role(
            self,
            "CrawlerRole",
            assumed_by=iam.ServicePrincipal("glue.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSGlueServiceRole")
            ],
        )
        for zone in CRAWLED_ZONES:
            self.data_bucket.grant_read(crawler_role, f"{zone}/*")

        for zone in CRAWLED_ZONES:
            crawler = glue.CfnCrawler(
                self,
                f"{zone.capitalize()}Crawler",
                name=f"outreach-{zone}",
                role=crawler_role.role_arn,
                database_name=GLUE_DATABASE_NAME,
                targets=glue.CfnCrawler.TargetsProperty(
                    s3_targets=[
                        glue.CfnCrawler.S3TargetProperty(
                            path=f"s3://{self.data_bucket.bucket_name}/{zone}/"
                        )
                    ]
                ),
            )
            crawler.add_resource_dependency(database)

        self.vpc = ec2.Vpc(
            self,
            "DataVpc",
            max_azs=2,
            nat_gateways=0,
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    name="isolated", subnet_type=ec2.SubnetType.PRIVATE_ISOLATED
                )
            ],
        )
        self.vpc.add_gateway_endpoint("S3Endpoint", service=ec2.GatewayVpcEndpointAwsService.S3)

        CfnOutput(self, "DataBucketName", value=self.data_bucket.bucket_name)
        CfnOutput(self, "AuditBucketName", value=self.audit_bucket.bucket_name)
