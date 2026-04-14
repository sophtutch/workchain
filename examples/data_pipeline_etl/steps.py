"""Step handlers, configs, and results for the Data Pipeline ETL workflow.

A 28-step modern data-lakehouse pipeline with five parallel ingestion roots,
per-source schema validation, a fan-in landing zone, PII masking, enrichment,
sessionization, aggregation, feature-store training, and fan-out to multiple
downstream sinks. Five async steps demonstrate polling completeness checks.

DAG overview::

    ingest_postgres_cdc ─┐                            ┌─ enrich_geoip ────────┐
    ingest_salesforce ───┤─► schema_validate_* ─┐     │                       │
    ingest_s3_events ────┤   (per source)       ├──►  │                       ├─► compute_sessions
    ingest_kafka_stream ─┤                      │     │                       │        │
    ingest_stripe ───────┘                      │     ├─ enrich_user_profiles ┘        │
                                                ▼     │                                │
                                      land_raw_parquet                                 │
                                                ▼                                      │
                                      deduplicate_records                              │
                                           │        │                                  │
                                           ▼        ▼                                  │
                                    quality_metrics  detect_pii                        │
                                                         │                             │
                                                         ▼                             │
                                                     mask_pii                          │
                                                    │       │                          │
                                                    ▼       ▼                          │
                                    normalize_currency  normalize_timestamps           │
                                                                                       │
                                                                     ┌─────────────────┤
                                                                     │                 │
                                                                     ▼                 ▼
                                                              aggregate_hourly   train_feature_store
                                                                     ▼                  │
                                                              aggregate_daily           │
                                                                     ▼                  │
                                                              compute_cohort_metrics    │
                                                                                        │
                    load_to_elasticsearch ◄── enrich_user_profiles                      │
                              │                                                        │
                              ▼                                                        ▼
                    publish_to_dashboard ◄── compute_cohort_metrics     load_to_snowflake
                              │                                                        │
                              └──────────────────────────► notify_downstream ◄─────────┘

Five async steps (enrich_geoip, enrich_user_profiles, train_feature_store,
load_to_snowflake, load_to_elasticsearch) demonstrate the claim → submit →
release → poll → complete cycle. Each uses a completeness check that reports
incremental progress across poll cycles.
"""

from __future__ import annotations

import asyncio
import logging
import random
import uuid
from typing import cast

from workchain import (
    CheckResult,
    PollPolicy,
    RetryPolicy,
    StepConfig,
    StepResult,
    async_step,
    completeness_check,
    step,
)

logger = logging.getLogger(__name__)

CATEGORY = "Data Pipeline ETL"

# ---------------------------------------------------------------------------
# Poll state (shared across async steps; keyed by job id)
# ---------------------------------------------------------------------------

_poll_counts: dict[str, int] = {}


def _handler_delay() -> float:
    """Realistic per-step work delay (5-20s)."""
    return random.uniform(5.0, 20.0)


def _poll_delay() -> float:
    """Per-poll completeness-check delay (2-5s)."""
    return random.uniform(2.0, 5.0)


# ===========================================================================
# Configs
# ===========================================================================


class IngestPostgresConfig(StepConfig):
    """CDC ingestion from a Postgres logical replication slot."""

    dsn: str = "postgres://pg-primary.prod.internal:5432/core"
    slot: str = "workchain_cdc_slot"
    max_lsn_batch: int = 50_000


class IngestSalesforceConfig(StepConfig):
    """Salesforce REST API incremental pull."""

    org_id: str = "00D5f000000TEST"
    soql: str = "SELECT Id, LastModifiedDate FROM Account WHERE SystemModstamp > LAST_RUN"
    page_size: int = 2_000


class IngestS3Config(StepConfig):
    """S3 event ingestion from a prefix partitioned by ingestion hour."""

    bucket: str = "acme-raw-events"
    prefix: str = "events/hour="
    region: str = "us-west-2"


class IngestKafkaConfig(StepConfig):
    """Kafka topic drain with a bounded window."""

    bootstrap: str = "kafka.prod.internal:9092"
    topic: str = "clickstream.v3"
    window_seconds: int = 300


class IngestStripeConfig(StepConfig):
    """Stripe billing event webhook replay."""

    api_key_id: str = "rk_live_REDACTED"
    since_cursor: str = "evt_1P0000000000000000000000"


class SchemaValidateConfig(StepConfig):
    """Generic schema validation config — expected columns per source."""

    strict: bool = True
    allow_extra_columns: bool = False


class LandRawConfig(StepConfig):
    """Landing zone for merged raw Parquet files."""

    lake_bucket: str = "acme-lake-bronze"
    compression: str = "snappy"
    partition_by: str = "ingest_date"


class DedupeConfig(StepConfig):
    """Record deduplication across merged sources."""

    key_columns: list[str] = ["source", "event_id"]
    window_hours: int = 24


class DetectPiiConfig(StepConfig):
    """PII detection scan thresholds."""

    detectors: list[str] = ["email", "phone", "ssn", "credit_card", "passport"]
    confidence_threshold: float = 0.85


class MaskPiiConfig(StepConfig):
    """PII masking strategy."""

    strategy: str = "deterministic_hash"
    hash_salt_secret: str = "secrets/pii-salt"


class QualityMetricsConfig(StepConfig):
    """Data quality scoring configuration."""

    completeness_threshold: float = 0.98
    freshness_threshold_seconds: int = 900


class NormalizeCurrencyConfig(StepConfig):
    """FX normalization against a reference currency."""

    reference_currency: str = "USD"
    fx_source: str = "ecb"
    fallback_rate_days: int = 7


class NormalizeTimestampsConfig(StepConfig):
    """Timezone-normalize all event timestamps."""

    target_timezone: str = "UTC"
    accept_formats: list[str] = ["iso8601", "epoch_ms", "epoch_s"]


class EnrichGeoipConfig(StepConfig):
    """GeoIP enrichment via an external lookup service."""

    provider: str = "maxmind"
    db_version: str = "GeoLite2-City"


class EnrichUserProfilesConfig(StepConfig):
    """Join events with the user dimension from the lakehouse silver layer."""

    user_dim_table: str = "silver.dim_user"
    join_key: str = "user_id"


class ComputeSessionsConfig(StepConfig):
    """Sessionization window and boundary rules."""

    idle_minutes: int = 30
    max_session_hours: int = 4


class AggregateHourlyConfig(StepConfig):
    """Hourly rollup metric set."""

    metrics: list[str] = ["events", "unique_users", "revenue_cents"]


class AggregateDailyConfig(StepConfig):
    """Daily rollup derived from hourly aggregates."""

    metrics: list[str] = ["events", "unique_users", "revenue_cents", "sessions"]


class CohortMetricsConfig(StepConfig):
    """Cohort analysis configuration."""

    cohort_by: str = "signup_week"
    retention_weeks: int = 12


class TrainFeatureStoreConfig(StepConfig):
    """Feature-store materialisation for ML consumers."""

    feature_group: str = "user_activity_v4"
    feature_version: int = 4
    offline_store: str = "s3://acme-feature-store/offline"


class LoadSnowflakeConfig(StepConfig):
    """Snowflake bulk load target."""

    account: str = "acme-analytics.us-west-2"
    warehouse: str = "ETL_LOAD_WH"
    database: str = "ANALYTICS"
    schema_: str = "EVENTS"


class LoadElasticsearchConfig(StepConfig):
    """Elasticsearch index target for search/dashboard traffic."""

    cluster: str = "https://es.prod.internal:9200"
    index_pattern: str = "events-{yyyy}.{MM}.{dd}"


class PublishDashboardConfig(StepConfig):
    """Dashboard cache refresh."""

    cache_service: str = "redis://dashboard-cache.prod:6379/1"
    ttl_seconds: int = 3_600


class NotifyDownstreamConfig(StepConfig):
    """Final notification fan-out."""

    slack_channel: str = "#data-platform"
    pagerduty_service: str = "PDXXXXXX"


# ===========================================================================
# Results
# ===========================================================================


class IngestResult(StepResult):
    """Common shape for ingestion handlers."""

    source: str = ""
    rows_ingested: int = 0
    bytes_ingested: int = 0
    watermark: str = ""


class SchemaValidateResult(StepResult):
    source: str = ""
    valid: bool = False
    columns_checked: int = 0
    violations: int = 0
    # Pass-through from the upstream IngestResult so the fan-in
    # step (land_raw_parquet) can aggregate totals without needing
    # to depend on the ingest steps directly.
    rows_ingested: int = 0
    bytes_ingested: int = 0


class LandRawResult(StepResult):
    parquet_uri: str = ""
    total_rows: int = 0
    total_bytes: int = 0
    file_count: int = 0


class DedupeResult(StepResult):
    rows_in: int = 0
    rows_out: int = 0
    duplicates_dropped: int = 0


class DetectPiiResult(StepResult):
    pii_columns: list[str] = []
    records_with_pii: int = 0


class MaskPiiResult(StepResult):
    records_masked: int = 0
    fields_masked: int = 0


class QualityMetricsResult(StepResult):
    completeness_score: float = 0.0
    freshness_seconds: int = 0
    anomalies: int = 0


class NormalizeCurrencyResult(StepResult):
    records_normalised: int = 0
    fx_rates_applied: int = 0


class NormalizeTimestampsResult(StepResult):
    records_normalised: int = 0
    invalid_skipped: int = 0


class EnrichGeoipResult(StepResult):
    job_id: str = ""
    records_enriched: int = 0
    geo_hit_rate: float = 0.0


class EnrichUserProfilesResult(StepResult):
    job_id: str = ""
    records_joined: int = 0
    null_join_rate: float = 0.0


class ComputeSessionsResult(StepResult):
    sessions_computed: int = 0
    avg_events_per_session: float = 0.0


class AggregateHourlyResult(StepResult):
    hours_computed: int = 0
    rows_written: int = 0


class AggregateDailyResult(StepResult):
    days_computed: int = 0
    rows_written: int = 0


class CohortMetricsResult(StepResult):
    cohorts_computed: int = 0
    retention_matrix_size: int = 0


class TrainFeatureStoreResult(StepResult):
    job_id: str = ""
    feature_count: int = 0
    offline_rows: int = 0


class LoadSnowflakeResult(StepResult):
    job_id: str = ""
    rows_loaded: int = 0
    target_table: str = ""


class LoadElasticsearchResult(StepResult):
    job_id: str = ""
    docs_indexed: int = 0
    indices_written: int = 0


class PublishDashboardResult(StepResult):
    cache_keys_refreshed: int = 0
    dashboards_invalidated: int = 0


class NotifyDownstreamResult(StepResult):
    channels_notified: int = 0
    recipients_emailed: int = 0


# ===========================================================================
# Ingestion roots (5 parallel sources)
# ===========================================================================


@step(
    category=CATEGORY,
    description="Drain Postgres logical replication slot for CDC events",
    retry=RetryPolicy(max_attempts=4, wait_seconds=1.0, wait_multiplier=2.0),
)
async def ingest_postgres_cdc(
    config: IngestPostgresConfig, _results: dict[str, StepResult]
) -> IngestResult:
    """Drain Postgres CDC events from a logical replication slot."""
    await asyncio.sleep(_handler_delay())
    rows = random.randint(40_000, 120_000)
    bytes_ingested = rows * random.randint(320, 640)
    watermark = f"LSN/{random.randint(10**11, 10**12):012x}"
    logger.info(
        "[ingest-pg] Drained %d rows (%d bytes) from slot=%s dsn=%s watermark=%s",
        rows, bytes_ingested, config.slot, config.dsn, watermark,
    )
    return IngestResult(
        source="postgres", rows_ingested=rows,
        bytes_ingested=bytes_ingested, watermark=watermark,
    )


@step(
    category=CATEGORY,
    description="Pull Salesforce records via incremental SOQL query",
    retry=RetryPolicy(max_attempts=4, wait_seconds=1.5, wait_multiplier=2.0),
)
async def ingest_salesforce_api(
    config: IngestSalesforceConfig, _results: dict[str, StepResult]
) -> IngestResult:
    """Pull Salesforce records via an incremental SOQL query."""
    await asyncio.sleep(_handler_delay())
    rows = random.randint(8_000, 30_000)
    bytes_ingested = rows * random.randint(1_200, 2_400)
    watermark = f"sfdc/{random.randint(10**14, 10**15)}"
    logger.info(
        "[ingest-sfdc] Pulled %d rows (%d bytes) org=%s page_size=%d watermark=%s",
        rows, bytes_ingested, config.org_id, config.page_size, watermark,
    )
    return IngestResult(
        source="salesforce", rows_ingested=rows,
        bytes_ingested=bytes_ingested, watermark=watermark,
    )


@step(
    category=CATEGORY,
    description="List and read event files from an S3 prefix",
    retry=RetryPolicy(max_attempts=5, wait_seconds=1.0, wait_multiplier=2.0),
)
async def ingest_s3_events(
    config: IngestS3Config, _results: dict[str, StepResult]
) -> IngestResult:
    """List and read event files from an S3 prefix."""
    await asyncio.sleep(_handler_delay())
    rows = random.randint(250_000, 800_000)
    bytes_ingested = rows * random.randint(180, 320)
    watermark = f"s3://{config.bucket}/{config.prefix}{random.randint(0, 23):02d}"
    logger.info(
        "[ingest-s3] Read %d events (%d bytes) from %s region=%s",
        rows, bytes_ingested, watermark, config.region,
    )
    return IngestResult(
        source="s3", rows_ingested=rows,
        bytes_ingested=bytes_ingested, watermark=watermark,
    )


@step(
    category=CATEGORY,
    description="Drain a bounded window from a Kafka topic",
    retry=RetryPolicy(max_attempts=4, wait_seconds=0.75, wait_multiplier=2.0),
)
async def ingest_kafka_stream(
    config: IngestKafkaConfig, _results: dict[str, StepResult]
) -> IngestResult:
    """Drain a bounded window from a Kafka topic."""
    await asyncio.sleep(_handler_delay())
    rows = random.randint(500_000, 1_500_000)
    bytes_ingested = rows * random.randint(220, 480)
    watermark = f"{config.topic}@{random.randint(10**9, 10**10)}"
    logger.info(
        "[ingest-kafka] Drained %d messages (%d bytes) window=%ss topic=%s",
        rows, bytes_ingested, config.window_seconds, config.topic,
    )
    return IngestResult(
        source="kafka", rows_ingested=rows,
        bytes_ingested=bytes_ingested, watermark=watermark,
    )


@step(
    category=CATEGORY,
    description="Replay Stripe webhook events since the last cursor",
    retry=RetryPolicy(max_attempts=4, wait_seconds=1.0, wait_multiplier=2.0),
)
async def ingest_stripe_webhooks(
    config: IngestStripeConfig, _results: dict[str, StepResult]
) -> IngestResult:
    """Replay Stripe webhook events since the last cursor."""
    await asyncio.sleep(_handler_delay())
    rows = random.randint(1_500, 8_000)
    bytes_ingested = rows * random.randint(2_500, 5_000)
    watermark = f"evt_{uuid.uuid4().hex[:24]}"
    logger.info(
        "[ingest-stripe] Replayed %d billing events (%d bytes) since=%s cursor=%s",
        rows, bytes_ingested, config.since_cursor, watermark,
    )
    return IngestResult(
        source="stripe", rows_ingested=rows,
        bytes_ingested=bytes_ingested, watermark=watermark,
    )


# ===========================================================================
# Per-source schema validation (5 parallel)
# ===========================================================================


def _validate_source(
    results: dict[str, StepResult], source_step: str, expected_columns: int,
) -> SchemaValidateResult:
    """Shared helper used by each schema_validate_* handler."""
    ingest = cast(IngestResult, results[source_step])
    violations = random.randint(0, max(1, ingest.rows_ingested // 100_000))
    valid = ingest.rows_ingested > 0
    logger.info(
        "[schema:%s] Validated %d rows against %d expected columns (%d violations)",
        ingest.source, ingest.rows_ingested, expected_columns, violations,
    )
    return SchemaValidateResult(
        source=ingest.source,
        valid=valid,
        columns_checked=expected_columns,
        violations=violations,
        rows_ingested=ingest.rows_ingested,
        bytes_ingested=ingest.bytes_ingested,
    )


@step(
    category=CATEGORY,
    description="Validate Postgres CDC payloads against the expected schema",
    depends_on=["ingest_postgres_cdc"],
)
async def schema_validate_postgres(
    _config: SchemaValidateConfig, results: dict[str, StepResult]
) -> SchemaValidateResult:
    """Validate Postgres CDC payloads against the expected schema."""
    await asyncio.sleep(_handler_delay())
    return _validate_source(results, "ingest_postgres_cdc", expected_columns=34)


@step(
    category=CATEGORY,
    description="Validate Salesforce records against the expected schema",
    depends_on=["ingest_salesforce_api"],
)
async def schema_validate_salesforce(
    _config: SchemaValidateConfig, results: dict[str, StepResult]
) -> SchemaValidateResult:
    """Validate Salesforce records against the expected schema."""
    await asyncio.sleep(_handler_delay())
    return _validate_source(results, "ingest_salesforce_api", expected_columns=51)


@step(
    category=CATEGORY,
    description="Validate S3 event files against the expected schema",
    depends_on=["ingest_s3_events"],
)
async def schema_validate_s3(
    _config: SchemaValidateConfig, results: dict[str, StepResult]
) -> SchemaValidateResult:
    """Validate S3 event files against the expected schema."""
    await asyncio.sleep(_handler_delay())
    return _validate_source(results, "ingest_s3_events", expected_columns=22)


@step(
    category=CATEGORY,
    description="Validate Kafka stream messages against the expected schema",
    depends_on=["ingest_kafka_stream"],
)
async def schema_validate_kafka(
    _config: SchemaValidateConfig, results: dict[str, StepResult]
) -> SchemaValidateResult:
    """Validate Kafka stream messages against the expected schema."""
    await asyncio.sleep(_handler_delay())
    return _validate_source(results, "ingest_kafka_stream", expected_columns=18)


@step(
    category=CATEGORY,
    description="Validate Stripe billing events against the expected schema",
    depends_on=["ingest_stripe_webhooks"],
)
async def schema_validate_stripe(
    _config: SchemaValidateConfig, results: dict[str, StepResult]
) -> SchemaValidateResult:
    """Validate Stripe billing events against the expected schema."""
    await asyncio.sleep(_handler_delay())
    return _validate_source(results, "ingest_stripe_webhooks", expected_columns=29)


# ===========================================================================
# Fan-in: land all validated sources to the bronze Parquet layer
# ===========================================================================


@step(
    category=CATEGORY,
    description="Merge all validated sources and land them as bronze Parquet",
    depends_on=[
        "schema_validate_postgres",
        "schema_validate_salesforce",
        "schema_validate_s3",
        "schema_validate_kafka",
        "schema_validate_stripe",
    ],
)
async def land_raw_parquet(
    config: LandRawConfig, results: dict[str, StepResult]
) -> LandRawResult:
    """Merge all validated source streams and write them as partitioned Parquet."""
    await asyncio.sleep(_handler_delay())

    # Ingest metrics flow through the schema_validate results (pass-through
    # fields) because the handler's direct dependencies are the validates,
    # not the ingests.
    validates = [
        cast(SchemaValidateResult, results["schema_validate_postgres"]),
        cast(SchemaValidateResult, results["schema_validate_salesforce"]),
        cast(SchemaValidateResult, results["schema_validate_s3"]),
        cast(SchemaValidateResult, results["schema_validate_kafka"]),
        cast(SchemaValidateResult, results["schema_validate_stripe"]),
    ]
    total_rows = sum(v.rows_ingested for v in validates)
    total_bytes = sum(v.bytes_ingested for v in validates)
    file_count = random.randint(12, 48)
    parquet_uri = (
        f"s3://{config.lake_bucket}/bronze/ingest_date={random.randint(10, 28):02d}/"
        f"part-{uuid.uuid4().hex[:8]}.parquet"
    )
    logger.info(
        "[land-raw] Merged %d rows / %.1f MB from %d sources into %d %s files at %s",
        total_rows, total_bytes / 1e6, len(validates), file_count,
        config.compression, parquet_uri,
    )
    return LandRawResult(
        parquet_uri=parquet_uri, total_rows=total_rows,
        total_bytes=total_bytes, file_count=file_count,
    )


# ===========================================================================
# Quality & PII branch
# ===========================================================================


@step(
    category=CATEGORY,
    description="Deduplicate merged records by (source, event_id) within a window",
    depends_on=["land_raw_parquet"],
)
async def deduplicate_records(
    config: DedupeConfig, results: dict[str, StepResult]
) -> DedupeResult:
    """Deduplicate merged records using a configurable key set + time window."""
    await asyncio.sleep(_handler_delay())
    landed = cast(LandRawResult, results["land_raw_parquet"])
    dup_rate = random.uniform(0.005, 0.04)
    duplicates = int(landed.total_rows * dup_rate)
    rows_out = landed.total_rows - duplicates
    logger.info(
        "[dedupe] Dedup %d→%d rows (%d duplicates) by %s over %dh window",
        landed.total_rows, rows_out, duplicates, config.key_columns, config.window_hours,
    )
    return DedupeResult(
        rows_in=landed.total_rows, rows_out=rows_out, duplicates_dropped=duplicates,
    )


@step(
    category=CATEGORY,
    description="Scan for PII fields across the deduplicated dataset",
    depends_on=["deduplicate_records"],
)
async def detect_pii(
    config: DetectPiiConfig, results: dict[str, StepResult]
) -> DetectPiiResult:
    """Scan the deduplicated dataset for PII fields above the confidence threshold."""
    await asyncio.sleep(_handler_delay())
    deduped = cast(DedupeResult, results["deduplicate_records"])
    detected = random.sample(
        config.detectors, k=random.randint(2, len(config.detectors)),
    )
    pii_rate = random.uniform(0.12, 0.28)
    records_with_pii = int(deduped.rows_out * pii_rate)
    logger.info(
        "[detect-pii] Scanned %d rows with %d detectors, %d records flagged (types=%s)",
        deduped.rows_out, len(config.detectors), records_with_pii, detected,
    )
    return DetectPiiResult(
        pii_columns=detected, records_with_pii=records_with_pii,
    )


@step(
    category=CATEGORY,
    description="Mask detected PII fields using a deterministic hash",
    depends_on=["detect_pii"],
    retry=RetryPolicy(max_attempts=3, wait_seconds=0.5),
)
async def mask_pii(
    config: MaskPiiConfig, results: dict[str, StepResult]
) -> MaskPiiResult:
    """Apply deterministic masking to every detected PII column."""
    await asyncio.sleep(_handler_delay())
    detected = cast(DetectPiiResult, results["detect_pii"])
    records_masked = detected.records_with_pii
    fields_masked = records_masked * len(detected.pii_columns)
    logger.info(
        "[mask-pii] Masked %d fields across %d records using strategy=%s (cols=%s)",
        fields_masked, records_masked, config.strategy, detected.pii_columns,
    )
    return MaskPiiResult(records_masked=records_masked, fields_masked=fields_masked)


@step(
    category=CATEGORY,
    description="Compute data-quality metrics (completeness, freshness, anomalies)",
    depends_on=["deduplicate_records"],
)
async def quality_metrics(
    config: QualityMetricsConfig, results: dict[str, StepResult]
) -> QualityMetricsResult:
    """Compute data-quality metrics in parallel with the PII branch."""
    await asyncio.sleep(_handler_delay())
    deduped = cast(DedupeResult, results["deduplicate_records"])
    completeness = random.uniform(0.96, 0.999)
    freshness = random.randint(120, 1_800)
    anomalies = random.randint(0, max(1, deduped.rows_out // 250_000))
    logger.info(
        "[quality] %d rows: completeness=%.3f freshness=%ds anomalies=%d (thresholds=%.2f/%ds)",
        deduped.rows_out, completeness, freshness, anomalies,
        config.completeness_threshold, config.freshness_threshold_seconds,
    )
    return QualityMetricsResult(
        completeness_score=completeness, freshness_seconds=freshness, anomalies=anomalies,
    )


# ===========================================================================
# Normalization branch
# ===========================================================================


@step(
    category=CATEGORY,
    description="Normalize all monetary fields to a reference currency using FX rates",
    depends_on=["mask_pii"],
)
async def normalize_currency(
    config: NormalizeCurrencyConfig, results: dict[str, StepResult]
) -> NormalizeCurrencyResult:
    """Normalize monetary fields to ``reference_currency`` using live FX rates."""
    await asyncio.sleep(_handler_delay())
    masked = cast(MaskPiiResult, results["mask_pii"])
    fx_rates_applied = random.randint(8, 22)
    logger.info(
        "[normalize-fx] Applied %d FX rates (source=%s) to %d records, target=%s",
        fx_rates_applied, config.fx_source, masked.records_masked, config.reference_currency,
    )
    return NormalizeCurrencyResult(
        records_normalised=masked.records_masked, fx_rates_applied=fx_rates_applied,
    )


@step(
    category=CATEGORY,
    description="Normalize timestamp fields to a target timezone",
    depends_on=["mask_pii"],
)
async def normalize_timestamps(
    config: NormalizeTimestampsConfig, results: dict[str, StepResult]
) -> NormalizeTimestampsResult:
    """Normalize timestamp fields to a single target timezone."""
    await asyncio.sleep(_handler_delay())
    masked = cast(MaskPiiResult, results["mask_pii"])
    invalid = random.randint(0, max(1, masked.records_masked // 10_000))
    normalised = masked.records_masked - invalid
    logger.info(
        "[normalize-ts] Normalised %d timestamps to %s (%d invalid skipped)",
        normalised, config.target_timezone, invalid,
    )
    return NormalizeTimestampsResult(
        records_normalised=normalised, invalid_skipped=invalid,
    )


# ===========================================================================
# Enrichment (async polling)
# ===========================================================================


@completeness_check()
async def check_enrich_geoip(
    _config: EnrichGeoipConfig,
    _results: dict[str, StepResult],
    result: EnrichGeoipResult,
) -> CheckResult:
    """Poll the GeoIP enrichment service for completion (3 polls total)."""
    await asyncio.sleep(_poll_delay())
    count = _poll_counts.get(result.job_id, 0) + 1
    _poll_counts[result.job_id] = count
    if count >= 3:
        logger.info("[enrich-geo] Job %s complete after %d polls", result.job_id, count)
        return CheckResult(complete=True, progress=1.0, message="GeoIP lookup done")
    progress = round(count / 3, 2)
    logger.info("[enrich-geo] Job %s poll %d/3 (%.0f%%)", result.job_id, count, progress * 100)
    return CheckResult(
        complete=False, progress=progress,
        message=f"Enriching batch {count}/3", retry_after=2.0,
    )


@async_step(
    completeness_check=check_enrich_geoip,
    poll=PollPolicy(interval=2.0, timeout=120.0, max_polls=12),
    category=CATEGORY,
    description="Submit GeoIP enrichment job and poll until complete",
    depends_on=["normalize_timestamps"],
)
async def enrich_geoip(
    config: EnrichGeoipConfig, results: dict[str, StepResult]
) -> EnrichGeoipResult:
    """Submit a GeoIP enrichment batch to an external service."""
    await asyncio.sleep(_handler_delay())
    ts = cast(NormalizeTimestampsResult, results["normalize_timestamps"])
    job_id = f"geo-{uuid.uuid4().hex[:12]}"
    hit_rate = random.uniform(0.91, 0.99)
    logger.info(
        "[enrich-geo] Submitted job %s for %d records (provider=%s, db=%s)",
        job_id, ts.records_normalised, config.provider, config.db_version,
    )
    return EnrichGeoipResult(
        job_id=job_id, records_enriched=ts.records_normalised, geo_hit_rate=hit_rate,
    )


@completeness_check()
async def check_enrich_user_profiles(
    _config: EnrichUserProfilesConfig,
    _results: dict[str, StepResult],
    result: EnrichUserProfilesResult,
) -> CheckResult:
    """Poll the user-profile join job for completion (4 polls total)."""
    await asyncio.sleep(_poll_delay())
    count = _poll_counts.get(result.job_id, 0) + 1
    _poll_counts[result.job_id] = count
    if count >= 4:
        logger.info("[enrich-user] Job %s complete after %d polls", result.job_id, count)
        return CheckResult(complete=True, progress=1.0, message="User join complete")
    progress = round(count / 4, 2)
    logger.info("[enrich-user] Job %s poll %d/4 (%.0f%%)", result.job_id, count, progress * 100)
    return CheckResult(
        complete=False, progress=progress,
        message=f"Joining batch {count}/4", retry_after=2.5,
    )


@async_step(
    completeness_check=check_enrich_user_profiles,
    poll=PollPolicy(interval=2.5, timeout=150.0, max_polls=15),
    category=CATEGORY,
    description="Join events with the user dimension via an async Spark job",
    depends_on=["normalize_currency", "normalize_timestamps"],
)
async def enrich_user_profiles(
    config: EnrichUserProfilesConfig, results: dict[str, StepResult]
) -> EnrichUserProfilesResult:
    """Submit a Spark job to join events with the user dimension."""
    await asyncio.sleep(_handler_delay())
    fx = cast(NormalizeCurrencyResult, results["normalize_currency"])
    ts = cast(NormalizeTimestampsResult, results["normalize_timestamps"])
    join_target = min(fx.records_normalised, ts.records_normalised)
    job_id = f"usr-{uuid.uuid4().hex[:12]}"
    null_rate = random.uniform(0.01, 0.05)
    logger.info(
        "[enrich-user] Submitted job %s joining %d records on %s against %s",
        job_id, join_target, config.join_key, config.user_dim_table,
    )
    return EnrichUserProfilesResult(
        job_id=job_id, records_joined=join_target, null_join_rate=null_rate,
    )


# ===========================================================================
# Sessionize and aggregate
# ===========================================================================


@step(
    category=CATEGORY,
    description="Sessionize enriched events into user sessions",
    depends_on=["enrich_geoip", "enrich_user_profiles"],
)
async def compute_sessions(
    config: ComputeSessionsConfig, results: dict[str, StepResult]
) -> ComputeSessionsResult:
    """Sessionize enriched events using an idle-gap window."""
    await asyncio.sleep(_handler_delay())
    geo = cast(EnrichGeoipResult, results["enrich_geoip"])
    usr = cast(EnrichUserProfilesResult, results["enrich_user_profiles"])
    events = min(geo.records_enriched, usr.records_joined)
    avg_events = random.uniform(6.5, 14.2)
    sessions = max(1, int(events / avg_events))
    logger.info(
        "[sessions] Computed %d sessions from %d events (avg=%.1f, idle=%dm)",
        sessions, events, avg_events, config.idle_minutes,
    )
    return ComputeSessionsResult(
        sessions_computed=sessions, avg_events_per_session=avg_events,
    )


@step(
    category=CATEGORY,
    description="Roll up sessions into hourly metric buckets",
    depends_on=["compute_sessions"],
)
async def aggregate_hourly(
    config: AggregateHourlyConfig, results: dict[str, StepResult]
) -> AggregateHourlyResult:
    """Roll up session data into hourly metric buckets."""
    await asyncio.sleep(_handler_delay())
    sessions = cast(ComputeSessionsResult, results["compute_sessions"])
    hours = random.randint(18, 24)
    rows = hours * len(config.metrics) * random.randint(50, 150)
    logger.info(
        "[agg-hour] Wrote %d hourly rows across %d metrics from %d sessions",
        rows, len(config.metrics), sessions.sessions_computed,
    )
    return AggregateHourlyResult(hours_computed=hours, rows_written=rows)


@step(
    category=CATEGORY,
    description="Roll up hourly aggregates into daily metrics",
    depends_on=["aggregate_hourly"],
)
async def aggregate_daily(
    config: AggregateDailyConfig, results: dict[str, StepResult]
) -> AggregateDailyResult:
    """Roll up hourly metrics into daily aggregates."""
    await asyncio.sleep(_handler_delay())
    hourly = cast(AggregateHourlyResult, results["aggregate_hourly"])
    days = max(1, hourly.hours_computed // 24)
    rows = days * len(config.metrics) * random.randint(20, 80)
    logger.info(
        "[agg-day] Wrote %d daily rows across %d metrics from %d hourly rows",
        rows, len(config.metrics), hourly.rows_written,
    )
    return AggregateDailyResult(days_computed=days, rows_written=rows)


@step(
    category=CATEGORY,
    description="Compute cohort retention matrix from daily aggregates",
    depends_on=["aggregate_daily", "quality_metrics"],
)
async def compute_cohort_metrics(
    config: CohortMetricsConfig, results: dict[str, StepResult]
) -> CohortMetricsResult:
    """Compute cohort retention matrix, gated on quality metrics."""
    await asyncio.sleep(_handler_delay())
    daily = cast(AggregateDailyResult, results["aggregate_daily"])
    quality = cast(QualityMetricsResult, results["quality_metrics"])
    cohorts = random.randint(6, 24)
    matrix_size = cohorts * config.retention_weeks
    logger.info(
        "[cohort] Computed %d cohorts (matrix=%d) by %s — quality %.3f",
        cohorts, matrix_size, config.cohort_by, quality.completeness_score,
    )
    return CohortMetricsResult(
        cohorts_computed=cohorts, retention_matrix_size=matrix_size,
    )


# ===========================================================================
# Feature store training (async polling)
# ===========================================================================


@completeness_check()
async def check_train_feature_store(
    _config: TrainFeatureStoreConfig,
    _results: dict[str, StepResult],
    result: TrainFeatureStoreResult,
) -> CheckResult:
    """Poll the feature-store materialization job (5 polls total)."""
    await asyncio.sleep(_poll_delay())
    count = _poll_counts.get(result.job_id, 0) + 1
    _poll_counts[result.job_id] = count
    if count >= 5:
        logger.info("[features] Job %s complete after %d polls", result.job_id, count)
        return CheckResult(complete=True, progress=1.0, message="Features materialised")
    progress = round(count / 5, 2)
    logger.info("[features] Job %s poll %d/5 (%.0f%%)", result.job_id, count, progress * 100)
    return CheckResult(
        complete=False, progress=progress,
        message=f"Materializing batch {count}/5", retry_after=3.0,
    )


@async_step(
    completeness_check=check_train_feature_store,
    poll=PollPolicy(interval=3.0, timeout=180.0, max_polls=18),
    category=CATEGORY,
    description="Materialize ML features into the offline feature store",
    depends_on=["compute_sessions", "quality_metrics"],
)
async def train_feature_store(
    config: TrainFeatureStoreConfig, results: dict[str, StepResult]
) -> TrainFeatureStoreResult:
    """Materialize ML features into the offline feature store."""
    await asyncio.sleep(_handler_delay())
    sessions = cast(ComputeSessionsResult, results["compute_sessions"])
    quality = cast(QualityMetricsResult, results["quality_metrics"])
    job_id = f"fs-{uuid.uuid4().hex[:12]}"
    feature_count = random.randint(48, 128)
    offline_rows = sessions.sessions_computed * random.randint(3, 8)
    logger.info(
        "[features] Submitted job %s for group=%s v%d (%d features, %d rows, quality=%.3f)",
        job_id, config.feature_group, config.feature_version,
        feature_count, offline_rows, quality.completeness_score,
    )
    return TrainFeatureStoreResult(
        job_id=job_id, feature_count=feature_count, offline_rows=offline_rows,
    )


# ===========================================================================
# Load sinks (two async polling steps in parallel)
# ===========================================================================


@completeness_check()
async def check_load_snowflake(
    _config: LoadSnowflakeConfig,
    _results: dict[str, StepResult],
    result: LoadSnowflakeResult,
) -> CheckResult:
    """Poll the Snowflake COPY job (4 polls total)."""
    await asyncio.sleep(_poll_delay())
    count = _poll_counts.get(result.job_id, 0) + 1
    _poll_counts[result.job_id] = count
    if count >= 4:
        logger.info("[snow] Job %s complete after %d polls", result.job_id, count)
        return CheckResult(complete=True, progress=1.0, message="Snowflake load complete")
    progress = round(count / 4, 2)
    logger.info("[snow] Job %s poll %d/4 (%.0f%%)", result.job_id, count, progress * 100)
    return CheckResult(
        complete=False, progress=progress,
        message=f"Loading slice {count}/4", retry_after=2.5,
    )


@async_step(
    completeness_check=check_load_snowflake,
    poll=PollPolicy(interval=2.5, timeout=180.0, max_polls=18),
    category=CATEGORY,
    description="Bulk load daily aggregates + features into Snowflake",
    depends_on=["aggregate_daily", "train_feature_store"],
    retry=RetryPolicy(max_attempts=4, wait_seconds=1.0, wait_multiplier=2.0),
)
async def load_to_snowflake(
    config: LoadSnowflakeConfig, results: dict[str, StepResult]
) -> LoadSnowflakeResult:
    """Submit a bulk load of daily aggregates and features to Snowflake."""
    await asyncio.sleep(_handler_delay())
    daily = cast(AggregateDailyResult, results["aggregate_daily"])
    features = cast(TrainFeatureStoreResult, results["train_feature_store"])
    job_id = f"snow-{uuid.uuid4().hex[:12]}"
    target_table = f"{config.database}.{config.schema_}.EVENTS_DAILY"
    rows_loaded = daily.rows_written + features.offline_rows
    logger.info(
        "[snow] Submitted COPY %s → %s (%d rows, wh=%s)",
        job_id, target_table, rows_loaded, config.warehouse,
    )
    return LoadSnowflakeResult(
        job_id=job_id, rows_loaded=rows_loaded, target_table=target_table,
    )


@completeness_check()
async def check_load_elasticsearch(
    _config: LoadElasticsearchConfig,
    _results: dict[str, StepResult],
    result: LoadElasticsearchResult,
) -> CheckResult:
    """Poll the Elasticsearch bulk-index job (3 polls total)."""
    await asyncio.sleep(_poll_delay())
    count = _poll_counts.get(result.job_id, 0) + 1
    _poll_counts[result.job_id] = count
    if count >= 3:
        logger.info("[es] Job %s complete after %d polls", result.job_id, count)
        return CheckResult(complete=True, progress=1.0, message="Elasticsearch index complete")
    progress = round(count / 3, 2)
    logger.info("[es] Job %s poll %d/3 (%.0f%%)", result.job_id, count, progress * 100)
    return CheckResult(
        complete=False, progress=progress,
        message=f"Indexing batch {count}/3", retry_after=2.0,
    )


@async_step(
    completeness_check=check_load_elasticsearch,
    poll=PollPolicy(interval=2.0, timeout=120.0, max_polls=12),
    category=CATEGORY,
    description="Bulk-index enriched events into Elasticsearch",
    depends_on=["enrich_user_profiles"],
    retry=RetryPolicy(max_attempts=3, wait_seconds=0.75, wait_multiplier=2.0),
)
async def load_to_elasticsearch(
    config: LoadElasticsearchConfig, results: dict[str, StepResult]
) -> LoadElasticsearchResult:
    """Submit a bulk-index job to Elasticsearch."""
    await asyncio.sleep(_handler_delay())
    usr = cast(EnrichUserProfilesResult, results["enrich_user_profiles"])
    job_id = f"es-{uuid.uuid4().hex[:12]}"
    indices = random.randint(1, 4)
    logger.info(
        "[es] Submitted bulk-index %s to %s (%d docs, %d indices)",
        job_id, config.cluster, usr.records_joined, indices,
    )
    return LoadElasticsearchResult(
        job_id=job_id, docs_indexed=usr.records_joined, indices_written=indices,
    )


# ===========================================================================
# Publish + notify
# ===========================================================================


@step(
    category=CATEGORY,
    description="Refresh dashboard cache after cohort and ES loads",
    depends_on=["compute_cohort_metrics", "load_to_elasticsearch"],
)
async def publish_to_dashboard(
    config: PublishDashboardConfig, results: dict[str, StepResult]
) -> PublishDashboardResult:
    """Refresh dashboard cache keys once cohorts and the ES index are ready."""
    await asyncio.sleep(_handler_delay())
    cohort = cast(CohortMetricsResult, results["compute_cohort_metrics"])
    es = cast(LoadElasticsearchResult, results["load_to_elasticsearch"])
    keys = random.randint(40, 180)
    dashboards = random.randint(6, 18)
    logger.info(
        "[dashboard] Refreshed %d cache keys / invalidated %d dashboards "
        "(cohorts=%d, es_docs=%d, ttl=%ds)",
        keys, dashboards, cohort.cohorts_computed, es.docs_indexed, config.ttl_seconds,
    )
    return PublishDashboardResult(
        cache_keys_refreshed=keys, dashboards_invalidated=dashboards,
    )


@step(
    category=CATEGORY,
    description="Notify downstream consumers and on-call channels",
    depends_on=["load_to_snowflake", "publish_to_dashboard"],
)
async def notify_downstream(
    config: NotifyDownstreamConfig, results: dict[str, StepResult]
) -> NotifyDownstreamResult:
    """Fan-out completion notifications to Slack / PagerDuty / email."""
    await asyncio.sleep(_handler_delay())
    snow = cast(LoadSnowflakeResult, results["load_to_snowflake"])
    dash = cast(PublishDashboardResult, results["publish_to_dashboard"])
    channels = 3  # slack + pagerduty + email
    recipients = random.randint(8, 32)
    logger.info(
        "[notify] Notified %d channels (%s, %s) / %d recipients — "
        "snowflake=%d rows, dashboard=%d keys",
        channels, config.slack_channel, config.pagerduty_service,
        recipients, snow.rows_loaded, dash.cache_keys_refreshed,
    )
    return NotifyDownstreamResult(
        channels_notified=channels, recipients_emailed=recipients,
    )
