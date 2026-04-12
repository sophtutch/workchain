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
    """ETL pipeline: extract → validate + transform → load → catalog."""
    return WorkflowTemplate(
        name="Data Pipeline ETL",
        description=(
            "Five-step ETL workflow: extract from source, validate schema, "
            "transform records, async load to warehouse, and catalog update."
        ),
        steps=[
            StepTemplate(
                name="extract_from_source",
                handler="examples.data_pipeline_etl.steps.extract_from_source",
                config={
                    "source_uri": "postgres://localhost:5432/source_db",
                    "table_name": "events",
                    "batch_size": 1000,
                },
                depends_on=[],
            ),
            StepTemplate(
                name="validate_schema",
                handler="examples.data_pipeline_etl.steps.validate_schema",
                config={
                    "expected_columns": ["id", "timestamp", "event_type", "payload"],
                    "strict": True,
                },
                depends_on=["extract_from_source"],
            ),
            StepTemplate(
                name="transform_records",
                handler="examples.data_pipeline_etl.steps.transform_records",
                config={},
                depends_on=["validate_schema", "extract_from_source"],
            ),
            StepTemplate(
                name="load_to_warehouse",
                handler="examples.data_pipeline_etl.steps.load_to_warehouse",
                config={
                    "warehouse_uri": "warehouse://localhost:5439/analytics",
                    "target_table": "events",
                },
                depends_on=["transform_records"],
            ),
            StepTemplate(
                name="update_catalog",
                handler="examples.data_pipeline_etl.steps.update_catalog",
                config={},
                depends_on=["load_to_warehouse"],
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
                config={"source_dir": "src"},
                depends_on=[],
            ),
            StepTemplate(
                name="run_unit_tests",
                handler="examples.ci_cd_pipeline.steps.run_unit_tests",
                config={"test_dir": "tests/unit", "coverage_threshold": 80.0},
                depends_on=["lint_code"],
            ),
            StepTemplate(
                name="security_scan",
                handler="examples.ci_cd_pipeline.steps.security_scan",
                config={"scan_profile": "strict"},
                depends_on=["lint_code"],
            ),
            StepTemplate(
                name="run_integration_tests",
                handler="examples.ci_cd_pipeline.steps.run_integration_tests",
                config={"db_url": "postgres://test/ci"},
                depends_on=["lint_code"],
            ),
            StepTemplate(
                name="license_audit",
                handler="examples.ci_cd_pipeline.steps.license_audit",
                config={"policy": "strict"},
                depends_on=["security_scan"],
            ),
            StepTemplate(
                name="vulnerability_report",
                handler="examples.ci_cd_pipeline.steps.vulnerability_report",
                config={"format": "sarif"},
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
                config={"registry": "ghcr.io"},
                depends_on=["build_artifact"],
            ),
            StepTemplate(
                name="compliance_sign_off",
                handler="examples.ci_cd_pipeline.steps.compliance_sign_off",
                config={"require_zero_critical": True},
                depends_on=["vulnerability_report"],
            ),
            StepTemplate(
                name="deploy_staging",
                handler="examples.ci_cd_pipeline.steps.deploy_staging",
                config={"environment": "staging"},
                depends_on=["push_to_registry"],
            ),
            StepTemplate(
                name="generate_report",
                handler="examples.ci_cd_pipeline.steps.generate_report",
                config={"include_coverage": True},
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
                config={"channel": "#ci-cd"},
                depends_on=["generate_report"],
            ),
            StepTemplate(
                name="update_dashboard",
                handler="examples.ci_cd_pipeline.steps.update_dashboard",
                config={"dashboard_id": "ci-main"},
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
                config={"resolution": "720p", "codec": "h264"},
                depends_on=["ingest_upload"],
            ),
            StepTemplate(
                name="transcode_1080p",
                handler="examples.media_processing.steps.transcode_1080p",
                config={"resolution": "1080p", "codec": "h264"},
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
                config={"train_ratio": 0.8},
                depends_on=["prepare_dataset"],
            ),
            StepTemplate(
                name="train_model",
                handler="examples.ml_training.steps.train_model",
                config={
                    "model_type": "resnet50",
                    "epochs": 100,
                    "learning_rate": 0.001,
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
                config={"cidr_block": "10.0.0.0/16", "region": "us-east-1"},
                depends_on=[],
            ),
            StepTemplate(
                name="provision_database",
                handler="examples.infra_provisioning.steps.provision_database",
                config={"engine": "postgres", "instance_class": "db.t3.medium"},
                depends_on=[],
            ),
            StepTemplate(
                name="deploy_application",
                handler="examples.infra_provisioning.steps.deploy_application",
                config={"image": "myorg/myapp:latest", "replicas": 2},
                depends_on=["create_vpc", "provision_database"],
            ),
            StepTemplate(
                name="configure_dns",
                handler="examples.infra_provisioning.steps.configure_dns",
                config={"domain": "app.example.com", "record_type": "A"},
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
                    "expected_status": 200,
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
                config={"carrier": "ups", "service_level": "ground"},
                depends_on=["pick_and_pack"],
            ),
            StepTemplate(
                name="send_confirmation",
                handler="examples.order_fulfillment.steps.send_confirmation",
                config={"include_tracking": True},
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
