from __future__ import annotations

# pyright: reportAttributeAccessIssue=false

import logging
import sys
import time
from pathlib import Path

import grpc
from django.conf import settings

# Ensure generated gRPC modules are importable when running from /Backend.
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.append(str(REPO_ROOT))

from shared_proto.python import content_service_pb2
from shared_proto.python import content_service_pb2_grpc

logger = logging.getLogger(__name__)


def _build_notes_text(response: content_service_pb2.ProcessTranscriptResponse) -> str:
    lines = []
    if response.summary:
        lines.append("TL;DR")
        lines.append(f"- {response.summary}")
        lines.append("")

    for section in response.sections:
        lines.append(section.heading or "Section")
        bullets = list(section.bullets)
        if bullets:
            lines.extend([f"- {item}" for item in bullets])
        else:
            lines.append("- (No details)")
        lines.append("")

    return "\n".join(lines).strip()


def process_transcript_via_grpc(
    transcript_text: str,
    source_url: str = "",
    title: str = "",
    max_sections: int = 6,
) -> str:
    target = f"{getattr(settings, 'CONTENT_SERVICE_HOST', 'content-service')}:{getattr(settings, 'CONTENT_SERVICE_PORT', 50051)}"
    timeout_seconds = float(getattr(settings, "CONTENT_SERVICE_TIMEOUT", 10))
    retries = int(getattr(settings, "CONTENT_SERVICE_RETRIES", 1))

    request = content_service_pb2.ProcessTranscriptRequest(
        transcript_text=transcript_text,
        source_url=source_url,
        title=title,
        max_sections=max_sections,
    )

    last_error: Exception | None = None

    for attempt in range(retries + 1):
        try:
            with grpc.insecure_channel(target) as channel:
                stub = content_service_pb2_grpc.ContentServiceStub(channel)
                response = stub.ProcessTranscript(
                    request,
                    timeout=timeout_seconds,
                    wait_for_ready=True,
                )

            if response.status and response.status.lower() != "ok":
                raise RuntimeError(
                    response.error_message or "content-service returned error"
                )

            logger.info("content-service success chunks=%s", response.chunk_count)
            return _build_notes_text(response)
        except Exception as exc:
            last_error = exc
            logger.warning(
                "content-service call failed attempt=%s/%s target=%s error=%s",
                attempt + 1,
                retries + 1,
                target,
                exc,
            )
            if attempt < retries:
                time.sleep(0.5)

    raise RuntimeError(f"content-service unavailable: {last_error}")
