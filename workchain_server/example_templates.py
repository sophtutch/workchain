"""Seed example workflow templates into the template store on startup.

Each example in ``examples/`` defines a ``build_workflow()`` function that
constructs a :class:`~workchain.models.Workflow` with fully wired steps,
configs, and dependency edges.  This module converts those workflow
definitions into :class:`~workchain.templates.WorkflowTemplate` objects and
upserts them into MongoDB so they appear in the designer's template list
immediately.

Templates are keyed by name — if a template with the same name already
exists it is left untouched, so user edits are never overwritten.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from workchain.templates import StepTemplate, WorkflowTemplate

if TYPE_CHECKING:
    from workchain.store import MongoWorkflowStore

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Template definitions
# ---------------------------------------------------------------------------

# Each entry is a WorkflowTemplate built by hand from the corresponding
# examples/**/workflow.py definition.  We avoid importing the example
# modules directly because they trigger handler registration side-effects
# and may not be installed in all environments.  Instead, the templates
# carry raw config dicts and handler dotted paths — identical to what the
# designer UI would submit.


def _customer_onboarding() -> WorkflowTemplate:
    """Customer onboarding: validate → create → provision → welcome email."""
    return WorkflowTemplate(
        name="Customer Onboarding",
        description=(
            "Four-step onboarding flow: validate email, create account with "
            "retry, provision resources asynchronously, and send a welcome email."
        ),
        steps=[
            StepTemplate(
                name="validate_email",
                handler="examples.customer_onboarding.steps.validate_email",
                config={"email": "user@example.com"},
                depends_on=[],
            ),
            StepTemplate(
                name="create_account",
                handler="examples.customer_onboarding.steps.create_account",
                config={},
                depends_on=["validate_email"],
            ),
            StepTemplate(
                name="provision_resources",
                handler="examples.customer_onboarding.steps.provision_resources",
                config={},
                depends_on=["create_account"],
            ),
            StepTemplate(
                name="send_welcome_email",
                handler="examples.customer_onboarding.steps.send_welcome_email",
                config={},
                depends_on=["validate_email", "create_account", "provision_resources"],
            ),
        ],
    )


def _data_pipeline_etl() -> WorkflowTemplate:
    """28-step data-lakehouse pipeline with 5 parallel ingests and 5 async jobs."""
    module = "examples.data_pipeline_etl.steps"

    def _handler(name: str) -> str:
        return f"{module}.{name}"

    return WorkflowTemplate(
        name="Data Pipeline ETL",
        description=(
            "28-step data-lakehouse pipeline: 5 parallel source ingests "
            "(Postgres CDC, Salesforce, S3, Kafka, Stripe), per-source schema "
            "validation, fan-in to a bronze Parquet landing zone, dedup + PII "
            "detection/masking + quality metrics, currency/timestamp "
            "normalization, async GeoIP and user-profile enrichment, "
            "sessionization, hourly/daily rollups, cohort metrics, async "
            "feature-store training, async Snowflake + Elasticsearch loads, "
            "dashboard cache refresh, and downstream notification."
        ),
        steps=[
            # --- Ingestion roots (5 parallel) ---
            StepTemplate(
                name="ingest_postgres_cdc",
                handler=_handler("ingest_postgres_cdc"),
                config={
                    "dsn": "postgres://pg-primary.prod.internal:5432/core",
                    "slot": "workchain_cdc_slot",
                    "max_lsn_batch": 50000,
                },
                depends_on=[],
            ),
            StepTemplate(
                name="ingest_salesforce_api",
                handler=_handler("ingest_salesforce_api"),
                config={"org_id": "00D5f000000TEST", "page_size": 2000},
                depends_on=[],
            ),
            StepTemplate(
                name="ingest_s3_events",
                handler=_handler("ingest_s3_events"),
                config={"bucket": "acme-raw-events", "region": "us-west-2"},
                depends_on=[],
            ),
            StepTemplate(
                name="ingest_kafka_stream",
                handler=_handler("ingest_kafka_stream"),
                config={
                    "bootstrap": "kafka.prod.internal:9092",
                    "topic": "clickstream.v3",
                    "window_seconds": 300,
                },
                depends_on=[],
            ),
            StepTemplate(
                name="ingest_stripe_webhooks",
                handler=_handler("ingest_stripe_webhooks"),
                config={},
                depends_on=[],
            ),
            # --- Per-source schema validation (5 parallel) ---
            StepTemplate(
                name="schema_validate_postgres",
                handler=_handler("schema_validate_postgres"),
                config={"strict": True},
                depends_on=["ingest_postgres_cdc"],
            ),
            StepTemplate(
                name="schema_validate_salesforce",
                handler=_handler("schema_validate_salesforce"),
                config={"strict": True},
                depends_on=["ingest_salesforce_api"],
            ),
            StepTemplate(
                name="schema_validate_s3",
                handler=_handler("schema_validate_s3"),
                config={"strict": True},
                depends_on=["ingest_s3_events"],
            ),
            StepTemplate(
                name="schema_validate_kafka",
                handler=_handler("schema_validate_kafka"),
                config={"strict": True},
                depends_on=["ingest_kafka_stream"],
            ),
            StepTemplate(
                name="schema_validate_stripe",
                handler=_handler("schema_validate_stripe"),
                config={"strict": True},
                depends_on=["ingest_stripe_webhooks"],
            ),
            # --- Fan-in to the bronze landing zone ---
            StepTemplate(
                name="land_raw_parquet",
                handler=_handler("land_raw_parquet"),
                config={
                    "lake_bucket": "acme-lake-bronze",
                    "compression": "snappy",
                    "partition_by": "ingest_date",
                },
                depends_on=[
                    "schema_validate_postgres",
                    "schema_validate_salesforce",
                    "schema_validate_s3",
                    "schema_validate_kafka",
                    "schema_validate_stripe",
                ],
            ),
            # --- Quality & PII branch ---
            StepTemplate(
                name="deduplicate_records",
                handler=_handler("deduplicate_records"),
                config={},
                depends_on=["land_raw_parquet"],
            ),
            StepTemplate(
                name="detect_pii",
                handler=_handler("detect_pii"),
                config={"confidence_threshold": 0.85},
                depends_on=["deduplicate_records"],
            ),
            StepTemplate(
                name="mask_pii",
                handler=_handler("mask_pii"),
                config={"strategy": "deterministic_hash"},
                depends_on=["detect_pii"],
            ),
            StepTemplate(
                name="quality_metrics",
                handler=_handler("quality_metrics"),
                config={
                    "completeness_threshold": 0.98,
                    "freshness_threshold_seconds": 900,
                },
                depends_on=["deduplicate_records"],
            ),
            # --- Normalization branch ---
            StepTemplate(
                name="normalize_currency",
                handler=_handler("normalize_currency"),
                config={"reference_currency": "USD", "fx_source": "ecb"},
                depends_on=["mask_pii"],
            ),
            StepTemplate(
                name="normalize_timestamps",
                handler=_handler("normalize_timestamps"),
                config={"target_timezone": "UTC"},
                depends_on=["mask_pii"],
            ),
            # --- Enrichment (async polling) ---
            StepTemplate(
                name="enrich_geoip",
                handler=_handler("enrich_geoip"),
                config={"provider": "maxmind", "db_version": "GeoLite2-City"},
                depends_on=["normalize_timestamps"],
            ),
            StepTemplate(
                name="enrich_user_profiles",
                handler=_handler("enrich_user_profiles"),
                config={
                    "user_dim_table": "silver.dim_user",
                    "join_key": "user_id",
                },
                depends_on=["normalize_currency", "normalize_timestamps"],
            ),
            # --- Sessionize and aggregate ---
            StepTemplate(
                name="compute_sessions",
                handler=_handler("compute_sessions"),
                config={"idle_minutes": 30, "max_session_hours": 4},
                depends_on=["enrich_geoip", "enrich_user_profiles"],
            ),
            StepTemplate(
                name="aggregate_hourly",
                handler=_handler("aggregate_hourly"),
                config={},
                depends_on=["compute_sessions"],
            ),
            StepTemplate(
                name="aggregate_daily",
                handler=_handler("aggregate_daily"),
                config={},
                depends_on=["aggregate_hourly"],
            ),
            StepTemplate(
                name="compute_cohort_metrics",
                handler=_handler("compute_cohort_metrics"),
                config={"cohort_by": "signup_week", "retention_weeks": 12},
                depends_on=["aggregate_daily", "quality_metrics"],
            ),
            # --- Feature store training (async polling) ---
            StepTemplate(
                name="train_feature_store",
                handler=_handler("train_feature_store"),
                config={
                    "feature_group": "user_activity_v4",
                    "feature_version": 4,
                    "offline_store": "s3://acme-feature-store/offline",
                },
                depends_on=["compute_sessions", "quality_metrics"],
            ),
            # --- Load sinks (two async polling steps in parallel) ---
            StepTemplate(
                name="load_to_snowflake",
                handler=_handler("load_to_snowflake"),
                config={
                    "account": "acme-analytics.us-west-2",
                    "warehouse": "ETL_LOAD_WH",
                    "database": "ANALYTICS",
                    "schema_": "EVENTS",
                },
                depends_on=["aggregate_daily", "train_feature_store"],
            ),
            StepTemplate(
                name="load_to_elasticsearch",
                handler=_handler("load_to_elasticsearch"),
                config={
                    "cluster": "https://es.prod.internal:9200",
                    "index_pattern": "events-{yyyy}.{MM}.{dd}",
                },
                depends_on=["enrich_user_profiles"],
            ),
            # --- Publish + notify ---
            StepTemplate(
                name="publish_to_dashboard",
                handler=_handler("publish_to_dashboard"),
                config={
                    "cache_service": "redis://dashboard-cache.prod:6379/1",
                    "ttl_seconds": 3600,
                },
                depends_on=["compute_cohort_metrics", "load_to_elasticsearch"],
            ),
            StepTemplate(
                name="notify_downstream",
                handler=_handler("notify_downstream"),
                config={
                    "slack_channel": "#data-platform",
                    "pagerduty_service": "PDXXXXXX",
                },
                depends_on=["load_to_snowflake", "publish_to_dashboard"],
            ),
        ],
    )


def _ci_cd_pipeline() -> WorkflowTemplate:
    """CI/CD: lint → 3 parallel lanes → report → notify + dashboard."""
    return WorkflowTemplate(
        name="CI/CD Pipeline",
        description=(
            "Thirteen-step CI/CD pipeline with asymmetric parallelism. "
            "After lint, fans out into unit tests, security scans, and "
            "integration/build/deploy lanes, joining at a report step."
        ),
        steps=[
            StepTemplate(
                name="lint_code",
                handler="examples.ci_cd_pipeline.steps.lint_code",
                config={},
                depends_on=[],
            ),
            StepTemplate(
                name="run_unit_tests",
                handler="examples.ci_cd_pipeline.steps.run_unit_tests",
                config={},
                depends_on=["lint_code"],
            ),
            StepTemplate(
                name="security_scan",
                handler="examples.ci_cd_pipeline.steps.security_scan",
                config={},
                depends_on=["lint_code"],
            ),
            StepTemplate(
                name="run_integration_tests",
                handler="examples.ci_cd_pipeline.steps.run_integration_tests",
                config={},
                depends_on=["lint_code"],
            ),
            StepTemplate(
                name="license_audit",
                handler="examples.ci_cd_pipeline.steps.license_audit",
                config={},
                depends_on=["security_scan"],
            ),
            StepTemplate(
                name="vulnerability_report",
                handler="examples.ci_cd_pipeline.steps.vulnerability_report",
                config={},
                depends_on=["security_scan"],
            ),
            StepTemplate(
                name="build_artifact",
                handler="examples.ci_cd_pipeline.steps.build_artifact",
                config={"repo": "myorg/myapp", "branch": "main"},
                depends_on=["run_integration_tests"],
            ),
            StepTemplate(
                name="push_to_registry",
                handler="examples.ci_cd_pipeline.steps.push_to_registry",
                config={},
                depends_on=["build_artifact"],
            ),
            StepTemplate(
                name="compliance_sign_off",
                handler="examples.ci_cd_pipeline.steps.compliance_sign_off",
                config={},
                depends_on=["vulnerability_report"],
            ),
            StepTemplate(
                name="deploy_staging",
                handler="examples.ci_cd_pipeline.steps.deploy_staging",
                config={},
                depends_on=["push_to_registry"],
            ),
            StepTemplate(
                name="generate_report",
                handler="examples.ci_cd_pipeline.steps.generate_report",
                config={},
                depends_on=[
                    "run_unit_tests",
                    "license_audit",
                    "compliance_sign_off",
                    "deploy_staging",
                ],
            ),
            StepTemplate(
                name="notify_team",
                handler="examples.ci_cd_pipeline.steps.notify_team",
                config={},
                depends_on=["generate_report"],
            ),
            StepTemplate(
                name="update_dashboard",
                handler="examples.ci_cd_pipeline.steps.update_dashboard",
                config={},
                depends_on=["generate_report"],
            ),
        ],
    )


def _media_processing() -> WorkflowTemplate:
    """Media processing: ingest → audio/video branches → package → publish."""
    return WorkflowTemplate(
        name="Media Processing",
        description=(
            "Thirteen-step media pipeline with nested parallelism and "
            "cross-branch joins. Ingests video, fans out to audio extraction "
            "and dual-resolution transcoding, then packages HLS and publishes."
        ),
        steps=[
            StepTemplate(
                name="ingest_upload",
                handler="examples.media_processing.steps.ingest_upload",
                config={"filename": "video.mp4", "content_type": "video/mp4"},
                depends_on=[],
            ),
            StepTemplate(
                name="extract_audio",
                handler="examples.media_processing.steps.extract_audio",
                config={},
                depends_on=["ingest_upload"],
            ),
            StepTemplate(
                name="transcode_720p",
                handler="examples.media_processing.steps.transcode_720p",
                config={},
                depends_on=["ingest_upload"],
            ),
            StepTemplate(
                name="transcode_1080p",
                handler="examples.media_processing.steps.transcode_1080p",
                config={},
                depends_on=["ingest_upload"],
            ),
            StepTemplate(
                name="normalize_audio",
                handler="examples.media_processing.steps.normalize_audio",
                config={},
                depends_on=["extract_audio"],
            ),
            StepTemplate(
                name="generate_waveform",
                handler="examples.media_processing.steps.generate_waveform",
                config={},
                depends_on=["extract_audio"],
            ),
            StepTemplate(
                name="thumbnail_720p",
                handler="examples.media_processing.steps.thumbnail_720p",
                config={},
                depends_on=["transcode_720p"],
            ),
            StepTemplate(
                name="thumbnail_1080p",
                handler="examples.media_processing.steps.thumbnail_1080p",
                config={},
                depends_on=["transcode_1080p"],
            ),
            StepTemplate(
                name="detect_faces",
                handler="examples.media_processing.steps.detect_faces",
                config={},
                depends_on=["thumbnail_720p", "thumbnail_1080p"],
            ),
            StepTemplate(
                name="generate_subtitles",
                handler="examples.media_processing.steps.generate_subtitles",
                config={},
                depends_on=["normalize_audio"],
            ),
            StepTemplate(
                name="package_hls",
                handler="examples.media_processing.steps.package_hls",
                config={},
                depends_on=["detect_faces", "generate_subtitles", "generate_waveform"],
            ),
            StepTemplate(
                name="publish_cdn",
                handler="examples.media_processing.steps.publish_cdn",
                config={},
                depends_on=["package_hls"],
            ),
            StepTemplate(
                name="update_catalog",
                handler="examples.media_processing.steps.update_catalog",
                config={},
                depends_on=["package_hls"],
            ),
        ],
    )


def _ml_training() -> WorkflowTemplate:
    """ML training: prepare → split → train (times out) → eval → publish."""
    return WorkflowTemplate(
        name="ML Training Pipeline",
        description=(
            "Five-step ML pipeline that demonstrates poll timeout failure. "
            "The training step never converges, triggering a timeout — "
            "downstream evaluation and publishing are never reached."
        ),
        steps=[
            StepTemplate(
                name="prepare_dataset",
                handler="examples.ml_training.steps.prepare_dataset",
                config={"dataset_name": "imagenet-mini", "sample_size": 10000},
                depends_on=[],
            ),
            StepTemplate(
                name="split_train_test",
                handler="examples.ml_training.steps.split_train_test",
                config={},
                depends_on=["prepare_dataset"],
            ),
            StepTemplate(
                name="train_model",
                handler="examples.ml_training.steps.train_model",
                config={
                    "model_type": "resnet50",
                },
                depends_on=["split_train_test"],
            ),
            StepTemplate(
                name="evaluate_model",
                handler="examples.ml_training.steps.evaluate_model",
                config={},
                depends_on=["train_model"],
            ),
            StepTemplate(
                name="publish_model",
                handler="examples.ml_training.steps.publish_model",
                config={},
                depends_on=["train_model"],
            ),
        ],
    )


def _incident_response() -> WorkflowTemplate:
    """Incident response: ticket → page → diagnose → remediate → verify → close."""
    return WorkflowTemplate(
        name="Incident Response",
        description=(
            "Six-step incident response workflow: create ticket, page on-call "
            "with retry, gather diagnostics, async remediation, verify "
            "resolution, and close the ticket."
        ),
        steps=[
            StepTemplate(
                name="create_ticket",
                handler="examples.incident_response.steps.create_ticket",
                config={
                    "service_name": "api-gateway",
                    "severity": "high",
                    "description": "Elevated error rates on API gateway",
                },
                depends_on=[],
            ),
            StepTemplate(
                name="page_oncall",
                handler="examples.incident_response.steps.page_oncall",
                config={
                    "service_name": "api-gateway",
                    "severity": "high",
                    "description": "Elevated error rates on API gateway",
                },
                depends_on=["create_ticket"],
            ),
            StepTemplate(
                name="gather_diagnostics",
                handler="examples.incident_response.steps.gather_diagnostics",
                config={
                    "service_name": "api-gateway",
                    "severity": "high",
                    "description": "Elevated error rates on API gateway",
                },
                depends_on=["page_oncall", "create_ticket"],
            ),
            StepTemplate(
                name="apply_remediation",
                handler="examples.incident_response.steps.apply_remediation",
                config={
                    "service_name": "api-gateway",
                    "severity": "high",
                    "description": "Elevated error rates on API gateway",
                },
                depends_on=["gather_diagnostics"],
            ),
            StepTemplate(
                name="verify_resolution",
                handler="examples.incident_response.steps.verify_resolution",
                config={
                    "service_name": "api-gateway",
                    "severity": "high",
                    "description": "Elevated error rates on API gateway",
                },
                depends_on=["apply_remediation"],
            ),
            StepTemplate(
                name="close_ticket",
                handler="examples.incident_response.steps.close_ticket",
                config={
                    "service_name": "api-gateway",
                    "severity": "high",
                    "description": "Elevated error rates on API gateway",
                },
                depends_on=["verify_resolution", "create_ticket"],
            ),
        ],
    )


def _infra_provisioning() -> WorkflowTemplate:
    """Infrastructure: VPC + DB in parallel → deploy → DNS → TLS → health."""
    return WorkflowTemplate(
        name="Infrastructure Provisioning",
        description=(
            "Six-step infrastructure provisioning with parallel root steps. "
            "Creates VPC and provisions database concurrently, then deploys "
            "the application, configures DNS, issues TLS cert, and runs a "
            "health check."
        ),
        steps=[
            StepTemplate(
                name="create_vpc",
                handler="examples.infra_provisioning.steps.create_vpc",
                config={"region": "us-east-1"},
                depends_on=[],
            ),
            StepTemplate(
                name="provision_database",
                handler="examples.infra_provisioning.steps.provision_database",
                config={},
                depends_on=[],
            ),
            StepTemplate(
                name="deploy_application",
                handler="examples.infra_provisioning.steps.deploy_application",
                config={"image": "myorg/myapp:latest"},
                depends_on=["create_vpc", "provision_database"],
            ),
            StepTemplate(
                name="configure_dns",
                handler="examples.infra_provisioning.steps.configure_dns",
                config={"domain": "app.example.com"},
                depends_on=["deploy_application"],
            ),
            StepTemplate(
                name="issue_tls_cert",
                handler="examples.infra_provisioning.steps.issue_tls_cert",
                config={"domain": "app.example.com"},
                depends_on=["configure_dns"],
            ),
            StepTemplate(
                name="health_check",
                handler="examples.infra_provisioning.steps.health_check",
                config={
                    "endpoint": "https://app.example.com/healthz",
                },
                depends_on=["issue_tls_cert"],
            ),
        ],
    )


def _order_fulfillment() -> WorkflowTemplate:
    """Order fulfillment: validate → inventory + shipping → pay → pack → ship → confirm."""
    return WorkflowTemplate(
        name="Order Fulfillment",
        description=(
            "Eight-step order pipeline: validate order, parallel inventory "
            "check and shipping calculation, async payment, reserve stock, "
            "pick and pack, async shipping arrangement, and confirmation email."
        ),
        steps=[
            StepTemplate(
                name="validate_order",
                handler="examples.order_fulfillment.steps.validate_order",
                config={
                    "order_id": "ORD-001",
                    "customer_email": "customer@example.com",
                    "line_items": [
                        {"sku": "WIDGET-A", "quantity": 2},
                        {"sku": "GADGET-B", "quantity": 1},
                    ],
                },
                depends_on=[],
            ),
            StepTemplate(
                name="check_inventory",
                handler="examples.order_fulfillment.steps.check_inventory",
                config={},
                depends_on=["validate_order"],
            ),
            StepTemplate(
                name="calculate_shipping",
                handler="examples.order_fulfillment.steps.calculate_shipping",
                config={
                    "destination_zip": "10001",
                    "shipping_method": "standard",
                },
                depends_on=["validate_order"],
            ),
            StepTemplate(
                name="process_payment",
                handler="examples.order_fulfillment.steps.process_payment",
                config={},
                depends_on=["check_inventory", "calculate_shipping"],
            ),
            StepTemplate(
                name="reserve_inventory",
                handler="examples.order_fulfillment.steps.reserve_inventory",
                config={},
                depends_on=["check_inventory", "process_payment"],
            ),
            StepTemplate(
                name="pick_and_pack",
                handler="examples.order_fulfillment.steps.pick_and_pack",
                config={},
                depends_on=["reserve_inventory"],
            ),
            StepTemplate(
                name="arrange_shipping",
                handler="examples.order_fulfillment.steps.arrange_shipping",
                config={"carrier": "ups"},
                depends_on=["pick_and_pack"],
            ),
            StepTemplate(
                name="send_confirmation",
                handler="examples.order_fulfillment.steps.send_confirmation",
                config={},
                depends_on=["validate_order", "arrange_shipping"],
            ),
        ],
    )


# ---------------------------------------------------------------------------
# All example templates
# ---------------------------------------------------------------------------

EXAMPLE_TEMPLATES: list[WorkflowTemplate] = [
    _customer_onboarding(),
    _data_pipeline_etl(),
    _ci_cd_pipeline(),
    _media_processing(),
    _ml_training(),
    _incident_response(),
    _infra_provisioning(),
    _order_fulfillment(),
]


# ---------------------------------------------------------------------------
# Seeding function (called from app.py lifespan)
# ---------------------------------------------------------------------------


async def seed_example_templates(store: MongoWorkflowStore) -> int:
    """Insert example templates that don't already exist.

    Templates are matched by name — if a template with the same name is
    already in the database it is skipped so that user edits are preserved.

    Args:
        store: The workflow store to seed templates into.

    Returns:
        The number of newly inserted templates.
    """
    existing = await store.list_templates(limit=500)
    existing_names = {t.name for t in existing}

    inserted = 0
    for template in EXAMPLE_TEMPLATES:
        if template.name in existing_names:
            logger.debug("Example template %r already exists — skipping", template.name)
            continue
        await store.insert_template(template)
        logger.info("Seeded example template: %s (%d steps)", template.name, len(template.steps))
        inserted += 1

    if inserted:
        logger.info("Seeded %d example template(s)", inserted)
    return inserted
