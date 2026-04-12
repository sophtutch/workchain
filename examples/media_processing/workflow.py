"""Build the media processing workflow definition.

Dependency graph (nested parallelism with cross-branch joins):

                      ┌→ normalize_audio → generate_subtitles ──┐
  ingest ──┬→ extract_audio                                      │
           │  └→ generate_waveform ─────────────────────────────┼→ package_hls ──┬→ publish_cdn
           │                                                     │                └→ update_catalog
           ├→ transcode_720p → thumbnail_720p ──┐               │
           │                                     ├→ detect_faces ┘
           └→ transcode_1080p → thumbnail_1080p ─┘

Six tiers of execution:
  T0: ingest_upload
  T1: extract_audio || transcode_720p || transcode_1080p          (3-wide root fan-out)
  T2: normalize_audio || generate_waveform || thumbnail_720p || thumbnail_1080p  (4-wide nested)
  T3: generate_subtitles || detect_faces                           (cross-branch join)
  T4: package_hls                                                  (major multi-branch join)
  T5: publish_cdn || update_catalog                                (final fan-out)
"""

from __future__ import annotations

from examples.media_processing import steps  # noqa: F401
from examples.media_processing.steps import IngestConfig
from workchain import PollPolicy, Step, Workflow


def build_workflow(
    filename: str = "video.mp4",
    content_type: str = "video/mp4",
) -> Workflow:
    """Construct a 13-step media processing pipeline with nested parallelism."""
    return Workflow(
        name="media_processing",
        steps=[
            # Tier 0: single root
            Step(
                name="ingest_upload",
                handler="examples.media_processing.steps.ingest_upload",
                config=IngestConfig(filename=filename, content_type=content_type),
            ),
            # Tier 1: 3-wide fan-out — audio, 720p, 1080p branches
            Step(
                name="extract_audio",
                handler="examples.media_processing.steps.extract_audio",
                depends_on=["ingest_upload"],
            ),
            Step(
                name="transcode_720p",
                handler="examples.media_processing.steps.transcode_720p",
                config={},
                is_async=True,
                completeness_check="examples.media_processing.steps.check_transcode",
                poll_policy=PollPolicy(
                    interval=2.0,
                    backoff_multiplier=1.0,
                    timeout=120.0,
                    max_polls=15,
                ),
                depends_on=["ingest_upload"],
            ),
            Step(
                name="transcode_1080p",
                handler="examples.media_processing.steps.transcode_1080p",
                config={},
                is_async=True,
                completeness_check="examples.media_processing.steps.check_transcode",
                poll_policy=PollPolicy(
                    interval=2.0,
                    backoff_multiplier=1.0,
                    timeout=120.0,
                    max_polls=15,
                ),
                depends_on=["ingest_upload"],
            ),
            # Tier 2: nested fan-out within branches
            #   Audio branch splits: normalize_audio || generate_waveform
            #   Video branches continue: thumbnail_720p, thumbnail_1080p
            Step(
                name="normalize_audio",
                handler="examples.media_processing.steps.normalize_audio",
                depends_on=["extract_audio"],
            ),
            Step(
                name="generate_waveform",
                handler="examples.media_processing.steps.generate_waveform",
                depends_on=["extract_audio"],
            ),
            Step(
                name="thumbnail_720p",
                handler="examples.media_processing.steps.thumbnail_720p",
                depends_on=["transcode_720p"],
            ),
            Step(
                name="thumbnail_1080p",
                handler="examples.media_processing.steps.thumbnail_1080p",
                depends_on=["transcode_1080p"],
            ),
            # Tier 3: cross-branch joins
            #   detect_faces joins the two video branches
            #   generate_subtitles continues the audio normalize path
            Step(
                name="detect_faces",
                handler="examples.media_processing.steps.detect_faces",
                depends_on=["thumbnail_720p", "thumbnail_1080p"],
            ),
            Step(
                name="generate_subtitles",
                handler="examples.media_processing.steps.generate_subtitles",
                depends_on=["normalize_audio"],
            ),
            # Tier 4: major multi-branch join
            Step(
                name="package_hls",
                handler="examples.media_processing.steps.package_hls",
                depends_on=["detect_faces", "generate_subtitles", "generate_waveform"],
            ),
            # Tier 5: final fan-out
            Step(
                name="publish_cdn",
                handler="examples.media_processing.steps.publish_cdn",
                depends_on=["package_hls"],
            ),
            Step(
                name="update_catalog",
                handler="examples.media_processing.steps.update_catalog",
                depends_on=["package_hls"],
            ),
        ],
    )
