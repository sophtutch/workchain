"""Step handlers for the media processing pipeline.

Dependency graph (nested parallelism with cross-branch joins):

                      ┌→ normalize_audio → generate_subtitles ──┐
  ingest ──┬→ extract_audio                                      │
           │  └→ generate_waveform ─────────────────────────────┼→ package_hls ──┬→ publish_cdn
           │                                                     │                └→ update_catalog
           ├→ transcode_720p → thumbnail_720p ──┐               │
           │                                     ├→ detect_faces ┘
           └→ transcode_1080p → thumbnail_1080p ─┘

Steps (13):
  Tier 0:  ingest_upload          - Validate and store raw upload
  Tier 1:  extract_audio          - Strip audio track
           transcode_720p         - Async 720p transcode
           transcode_1080p        - Async 1080p transcode
  Tier 2:  normalize_audio        - Normalize audio levels      (nested parallel within audio branch)
           generate_waveform      - Audio waveform visualization (nested parallel within audio branch)
           thumbnail_720p         - Extract poster from 720p
           thumbnail_1080p        - Extract poster from 1080p
  Tier 3:  generate_subtitles     - Auto-generate subtitles from audio
           detect_faces           - Face detection across thumbnails (cross-branch join)
  Tier 4:  package_hls            - Package into HLS streaming format (major multi-branch join)
  Tier 5:  publish_cdn            - Push to CDN
           update_catalog         - Register in media catalog
"""

from __future__ import annotations

import logging
import random
import uuid
from typing import cast

from workchain import (
    CheckResult,
    PollPolicy,
    StepConfig,
    StepResult,
    async_step,
    completeness_check,
    step,
)

logger = logging.getLogger(__name__)
_rng = random.SystemRandom()


def _asset_id(path: str) -> str:
    """Extract asset_id from paths like /processed/{asset_id}/file.ext."""
    return path.split("/")[2]

# ---------------------------------------------------------------------------
# Configs and Results
# ---------------------------------------------------------------------------


class IngestConfig(StepConfig):
    filename: str = "video.mp4"
    content_type: str = "video/mp4"


class IngestResult(StepResult):
    asset_id: str = ""
    storage_path: str = ""
    duration_seconds: float = 0.0


class AudioResult(StepResult):
    audio_path: str = ""
    codec: str = "aac"


class NormalizeResult(StepResult):
    normalized_path: str = ""
    peak_db: float = 0.0


class WaveformResult(StepResult):
    waveform_url: str = ""


class TranscodeConfig(StepConfig):
    resolution: str = "720p"
    codec: str = "h264"


class TranscodeResult(StepResult):
    job_id: str = ""
    output_path: str = ""


class ThumbnailResult(StepResult):
    thumbnail_url: str = ""
    dimensions: str = "1280x720"


class FaceDetectResult(StepResult):
    faces_found: int = 0
    bounding_boxes: list[dict] = []


class SubtitleResult(StepResult):
    subtitle_path: str = ""
    language: str = "en"
    segments: int = 0


class PackageResult(StepResult):
    manifest_url: str = ""
    segment_count: int = 0


class CdnResult(StepResult):
    cdn_url: str = ""
    cache_key: str = ""


class CatalogResult(StepResult):
    catalog_id: str = ""
    indexed: bool = False


# ---------------------------------------------------------------------------
# Step handlers
# ---------------------------------------------------------------------------


@step()
async def ingest_upload(
    config: IngestConfig,
    _results: dict[str, StepResult],
) -> IngestResult:
    """Validate and store the raw upload."""
    asset_id = f"asset-{uuid.uuid4().hex[:8]}"
    storage_path = f"/uploads/{asset_id}/{config.filename}"
    duration = round(_rng.uniform(30.0, 600.0), 1)
    logger.info("[ingest] Stored %s at %s (%.1fs)", config.filename, storage_path, duration)
    return IngestResult(asset_id=asset_id, storage_path=storage_path, duration_seconds=duration)


# --- Audio branch (extract → normalize || waveform) ---


@step()
async def extract_audio(
    _config: StepConfig | None,
    results: dict[str, StepResult],
) -> AudioResult:
    """Extract the audio track from the uploaded video."""
    ingest = cast(IngestResult, results["ingest_upload"])
    audio_path = f"/processed/{ingest.asset_id}/audio.aac"
    logger.info("[audio] Extracted audio from %s", ingest.storage_path)
    return AudioResult(audio_path=audio_path, codec="aac")


@step()
async def normalize_audio(
    _config: StepConfig | None,
    results: dict[str, StepResult],
) -> NormalizeResult:
    """Normalize audio levels to target loudness."""
    audio = cast(AudioResult, results["extract_audio"])
    normalized_path = audio.audio_path.replace(".aac", "_norm.aac")
    peak_db = round(_rng.uniform(-3.0, -0.5), 1)
    logger.info("[audio] Normalized %s (peak=%.1f dB)", normalized_path, peak_db)
    return NormalizeResult(normalized_path=normalized_path, peak_db=peak_db)


@step()
async def generate_waveform(
    _config: StepConfig | None,
    results: dict[str, StepResult],
) -> WaveformResult:
    """Generate an audio waveform visualization image."""
    audio = cast(AudioResult, results["extract_audio"])
    asset_id = _asset_id(audio.audio_path)
    waveform_url = f"/processed/{asset_id}/waveform.png"
    logger.info("[waveform] Generated waveform for %s", audio.audio_path)
    return WaveformResult(waveform_url=waveform_url)


# --- Video branches (transcode → thumbnail) ---


@completeness_check()
async def check_transcode(
    _config: TranscodeConfig,
    _results: dict[str, StepResult],
    result: TranscodeResult,
) -> CheckResult:
    """Completeness check: transcode finishes with 33% chance per poll."""
    if _rng.random() < 0.33:
        logger.info("[transcode] Job %s still encoding...", result.job_id)
        return CheckResult(complete=False, progress=round(_rng.uniform(0.2, 0.8), 2), message="Encoding")
    logger.info("[transcode] Job %s complete!", result.job_id)
    return CheckResult(complete=True, progress=1.0, message="Transcode finished")


@async_step(
    completeness_check=check_transcode,
    poll=PollPolicy(interval=2.0, backoff_multiplier=1.0, timeout=120.0, max_polls=15),
)
async def transcode_720p(
    config: TranscodeConfig,
    results: dict[str, StepResult],
) -> TranscodeResult:
    """Submit 720p transcode job."""
    ingest = cast(IngestResult, results["ingest_upload"])
    job_id = f"tx-720-{uuid.uuid4().hex[:8]}"
    output = f"/processed/{ingest.asset_id}/720p.mp4"
    logger.info("[transcode] Submitted 720p job %s for %s", job_id, ingest.asset_id)
    return TranscodeResult(job_id=job_id, output_path=output)


@async_step(
    completeness_check=check_transcode,
    poll=PollPolicy(interval=2.0, backoff_multiplier=1.0, timeout=120.0, max_polls=15),
)
async def transcode_1080p(
    config: TranscodeConfig,
    results: dict[str, StepResult],
) -> TranscodeResult:
    """Submit 1080p transcode job."""
    ingest = cast(IngestResult, results["ingest_upload"])
    job_id = f"tx-1080-{uuid.uuid4().hex[:8]}"
    output = f"/processed/{ingest.asset_id}/1080p.mp4"
    logger.info("[transcode] Submitted 1080p job %s for %s", job_id, ingest.asset_id)
    return TranscodeResult(job_id=job_id, output_path=output)


@step()
async def thumbnail_720p(
    _config: StepConfig | None,
    results: dict[str, StepResult],
) -> ThumbnailResult:
    """Extract poster thumbnail from the 720p transcode."""
    tx = cast(TranscodeResult, results["transcode_720p"])
    asset_id = _asset_id(tx.output_path)
    url = f"/processed/{asset_id}/thumb_720p.jpg"
    logger.info("[thumbnail] Generated 720p thumbnail from %s", tx.output_path)
    return ThumbnailResult(thumbnail_url=url, dimensions="1280x720")


@step()
async def thumbnail_1080p(
    _config: StepConfig | None,
    results: dict[str, StepResult],
) -> ThumbnailResult:
    """Extract poster thumbnail from the 1080p transcode."""
    tx = cast(TranscodeResult, results["transcode_1080p"])
    asset_id = _asset_id(tx.output_path)
    url = f"/processed/{asset_id}/thumb_1080p.jpg"
    logger.info("[thumbnail] Generated 1080p thumbnail from %s", tx.output_path)
    return ThumbnailResult(thumbnail_url=url, dimensions="1920x1080")


# --- Cross-branch joins ---


@step()
async def detect_faces(
    _config: StepConfig | None,
    results: dict[str, StepResult],
) -> FaceDetectResult:
    """Run face detection across both resolution thumbnails."""
    thumb_720 = cast(ThumbnailResult, results["thumbnail_720p"])
    thumb_1080 = cast(ThumbnailResult, results["thumbnail_1080p"])
    face_count = _rng.randint(0, 5)
    boxes = [{"x": _rng.randint(0, 100), "y": _rng.randint(0, 100), "w": 50, "h": 50} for _ in range(face_count)]
    logger.info("[faces] Detected %d faces across %s, %s", face_count, thumb_720.thumbnail_url, thumb_1080.thumbnail_url)
    return FaceDetectResult(faces_found=face_count, bounding_boxes=boxes)


@step()
async def generate_subtitles(
    _config: StepConfig | None,
    results: dict[str, StepResult],
) -> SubtitleResult:
    """Auto-generate subtitles from the normalized audio track."""
    norm = cast(NormalizeResult, results["normalize_audio"])
    asset_id = _asset_id(norm.normalized_path)
    subtitle_path = f"/processed/{asset_id}/subtitles.vtt"
    segments = _rng.randint(20, 200)
    logger.info("[subtitles] Generated %d segments from %s", segments, norm.normalized_path)
    return SubtitleResult(subtitle_path=subtitle_path, language="en", segments=segments)


# --- Major join → final fan-out ---


@step()
async def package_hls(
    _config: StepConfig | None,
    results: dict[str, StepResult],
) -> PackageResult:
    """Package all assets into HLS streaming format."""
    faces = cast(FaceDetectResult, results["detect_faces"])
    subs = cast(SubtitleResult, results["generate_subtitles"])
    waveform = cast(WaveformResult, results["generate_waveform"])
    asset_id = _asset_id(subs.subtitle_path)
    manifest_url = f"/processed/{asset_id}/master.m3u8"
    segments = _rng.randint(50, 300)
    logger.info(
        "[hls] Packaged %s: %d segments, %d faces, %d subtitle segments, waveform=%s",
        manifest_url, segments, faces.faces_found, subs.segments, waveform.waveform_url,
    )
    return PackageResult(manifest_url=manifest_url, segment_count=segments)


@step()
async def publish_cdn(
    _config: StepConfig | None,
    results: dict[str, StepResult],
) -> CdnResult:
    """Push packaged assets to the CDN."""
    pkg = cast(PackageResult, results["package_hls"])
    asset_id = _asset_id(pkg.manifest_url)
    cdn_url = f"https://cdn.example.com/{asset_id}"
    cache_key = uuid.uuid4().hex[:12]
    logger.info("[cdn] Published %s (cache_key=%s)", cdn_url, cache_key)
    return CdnResult(cdn_url=cdn_url, cache_key=cache_key)


@step()
async def update_catalog(
    _config: StepConfig | None,
    results: dict[str, StepResult],
) -> CatalogResult:
    """Register the processed media in the catalog."""
    pkg = cast(PackageResult, results["package_hls"])
    asset_id = _asset_id(pkg.manifest_url)
    catalog_id = f"cat-{uuid.uuid4().hex[:8]}"
    logger.info("[catalog] Indexed %s as %s", asset_id, catalog_id)
    return CatalogResult(catalog_id=catalog_id, indexed=True)
