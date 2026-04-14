"""Workflow builder for the Data Pipeline ETL example.

Constructs a 28-step data-lakehouse workflow. Step dependencies, retry
policies, poll policies, async-ness, and completeness checks are all
populated automatically from the decorators on ``steps.py`` — this
builder only supplies the per-step configs (defaults for most, with
override parameters for the handful of steps that take infrastructure
connection details).
"""

from __future__ import annotations

from examples.data_pipeline_etl.steps import (
    AggregateDailyConfig,
    AggregateHourlyConfig,
    CohortMetricsConfig,
    ComputeSessionsConfig,
    DedupeConfig,
    DetectPiiConfig,
    EnrichGeoipConfig,
    EnrichUserProfilesConfig,
    IngestKafkaConfig,
    IngestPostgresConfig,
    IngestS3Config,
    IngestSalesforceConfig,
    IngestStripeConfig,
    LandRawConfig,
    LoadElasticsearchConfig,
    LoadSnowflakeConfig,
    MaskPiiConfig,
    NormalizeCurrencyConfig,
    NormalizeTimestampsConfig,
    NotifyDownstreamConfig,
    PublishDashboardConfig,
    QualityMetricsConfig,
    SchemaValidateConfig,
    TrainFeatureStoreConfig,
)
from workchain import Step, Workflow

_STEP_MODULE = "examples.data_pipeline_etl.steps"


def _h(name: str) -> str:
    return f"{_STEP_MODULE}.{name}"


def build_workflow(
    *,
    postgres_dsn: str = "postgres://pg-primary.prod.internal:5432/core",
    kafka_bootstrap: str = "kafka.prod.internal:9092",
    s3_bucket: str = "acme-raw-events",
    lake_bucket: str = "acme-lake-bronze",
    snowflake_warehouse: str = "ETL_LOAD_WH",
) -> Workflow:
    """Build the 28-step data-pipeline workflow.

    All step dependencies, retry policies, and async polling metadata
    flow automatically from the handler decorators via Workflow model
    validation. The caller provides per-step configs (defaults for
    most, with explicit infrastructure overrides for the ingestion,
    landing, and load steps).
    """
    return Workflow(
        name="data_pipeline_etl",
        steps=[
            # --- Ingestion roots (5 parallel) ---
            # Each ingest is a DAG root: pass depends_on=[] explicitly
            # because the Workflow validator can't distinguish "unset"
            # from "empty list" via the decorator auto-copy path — the
            # sequential default would otherwise chain them.
            Step(
                name="ingest_postgres_cdc",
                handler=_h("ingest_postgres_cdc"),
                config=IngestPostgresConfig(dsn=postgres_dsn),
                depends_on=[],
            ),
            Step(
                name="ingest_salesforce_api",
                handler=_h("ingest_salesforce_api"),
                config=IngestSalesforceConfig(),
                depends_on=[],
            ),
            Step(
                name="ingest_s3_events",
                handler=_h("ingest_s3_events"),
                config=IngestS3Config(bucket=s3_bucket),
                depends_on=[],
            ),
            Step(
                name="ingest_kafka_stream",
                handler=_h("ingest_kafka_stream"),
                config=IngestKafkaConfig(bootstrap=kafka_bootstrap),
                depends_on=[],
            ),
            Step(
                name="ingest_stripe_webhooks",
                handler=_h("ingest_stripe_webhooks"),
                config=IngestStripeConfig(),
                depends_on=[],
            ),
            # --- Per-source schema validation (5 parallel) ---
            Step(
                name="schema_validate_postgres",
                handler=_h("schema_validate_postgres"),
                config=SchemaValidateConfig(),
            ),
            Step(
                name="schema_validate_salesforce",
                handler=_h("schema_validate_salesforce"),
                config=SchemaValidateConfig(),
            ),
            Step(
                name="schema_validate_s3",
                handler=_h("schema_validate_s3"),
                config=SchemaValidateConfig(),
            ),
            Step(
                name="schema_validate_kafka",
                handler=_h("schema_validate_kafka"),
                config=SchemaValidateConfig(),
            ),
            Step(
                name="schema_validate_stripe",
                handler=_h("schema_validate_stripe"),
                config=SchemaValidateConfig(),
            ),
            # --- Fan-in to the bronze landing zone ---
            Step(
                name="land_raw_parquet",
                handler=_h("land_raw_parquet"),
                config=LandRawConfig(lake_bucket=lake_bucket),
            ),
            # --- Quality & PII branch ---
            Step(
                name="deduplicate_records",
                handler=_h("deduplicate_records"),
                config=DedupeConfig(),
            ),
            Step(
                name="detect_pii",
                handler=_h("detect_pii"),
                config=DetectPiiConfig(),
            ),
            Step(
                name="mask_pii",
                handler=_h("mask_pii"),
                config=MaskPiiConfig(),
            ),
            Step(
                name="quality_metrics",
                handler=_h("quality_metrics"),
                config=QualityMetricsConfig(),
            ),
            # --- Normalization branch ---
            Step(
                name="normalize_currency",
                handler=_h("normalize_currency"),
                config=NormalizeCurrencyConfig(),
            ),
            Step(
                name="normalize_timestamps",
                handler=_h("normalize_timestamps"),
                config=NormalizeTimestampsConfig(),
            ),
            # --- Enrichment (async polling) ---
            Step(
                name="enrich_geoip",
                handler=_h("enrich_geoip"),
                config=EnrichGeoipConfig(),
            ),
            Step(
                name="enrich_user_profiles",
                handler=_h("enrich_user_profiles"),
                config=EnrichUserProfilesConfig(),
            ),
            # --- Sessionize and aggregate ---
            Step(
                name="compute_sessions",
                handler=_h("compute_sessions"),
                config=ComputeSessionsConfig(),
            ),
            Step(
                name="aggregate_hourly",
                handler=_h("aggregate_hourly"),
                config=AggregateHourlyConfig(),
            ),
            Step(
                name="aggregate_daily",
                handler=_h("aggregate_daily"),
                config=AggregateDailyConfig(),
            ),
            Step(
                name="compute_cohort_metrics",
                handler=_h("compute_cohort_metrics"),
                config=CohortMetricsConfig(),
            ),
            # --- Feature store training (async polling) ---
            Step(
                name="train_feature_store",
                handler=_h("train_feature_store"),
                config=TrainFeatureStoreConfig(),
            ),
            # --- Load sinks (two async polling steps in parallel) ---
            Step(
                name="load_to_snowflake",
                handler=_h("load_to_snowflake"),
                config=LoadSnowflakeConfig(warehouse=snowflake_warehouse),
            ),
            Step(
                name="load_to_elasticsearch",
                handler=_h("load_to_elasticsearch"),
                config=LoadElasticsearchConfig(),
            ),
            # --- Publish + notify ---
            Step(
                name="publish_to_dashboard",
                handler=_h("publish_to_dashboard"),
                config=PublishDashboardConfig(),
            ),
            Step(
                name="notify_downstream",
                handler=_h("notify_downstream"),
                config=NotifyDownstreamConfig(),
            ),
        ],
    )
