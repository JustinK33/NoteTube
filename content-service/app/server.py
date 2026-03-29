from __future__ import annotations

# pyright: reportAttributeAccessIssue=false

import logging
import os
import sys
from concurrent import futures
from pathlib import Path

import grpc

from processor import process_transcript

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.append(str(REPO_ROOT))

from shared_proto.python import content_service_pb2
from shared_proto.python import content_service_pb2_grpc

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s [content-service] %(message)s",
)
logger = logging.getLogger(__name__)


class ContentService(content_service_pb2_grpc.ContentServiceServicer):
    def ProcessTranscript(self, request, context):
        logger.info(
            "ProcessTranscript called title=%s source=%s",
            request.title,
            request.source_url,
        )

        try:
            result = process_transcript(
                transcript_text=request.transcript_text,
                max_sections=request.max_sections or 5,
            )

            sections = []
            for section in result.sections:
                sections.append(
                    content_service_pb2.NoteSection(
                        heading=section["heading"],
                        bullets=section["bullets"],
                    )
                )

            return content_service_pb2.ProcessTranscriptResponse(
                summary=result.summary,
                sections=sections,
                chunk_count=result.chunk_count,
                status="ok",
                error_message="",
            )
        except Exception as exc:
            logger.exception("ProcessTranscript failed: %s", exc)
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(exc))
            return content_service_pb2.ProcessTranscriptResponse(
                summary="",
                sections=[],
                chunk_count=0,
                status="error",
                error_message=str(exc),
            )

    def HealthCheck(self, request, context):
        logger.info(
            "HealthCheck called caller=%s",
            request.caller,
        )
        return content_service_pb2.HealthCheckResponse(
            status="healthy",
            service="content-service",
        )


def serve() -> None:
    port = int(os.getenv("CONTENT_SERVICE_PORT", "50051"))
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    content_service_pb2_grpc.add_ContentServiceServicer_to_server(
        ContentService(), server
    )
    server.add_insecure_port(f"[::]:{port}")

    logger.info("Starting content-service on 0.0.0.0:%s", port)
    server.start()
    server.wait_for_termination()


if __name__ == "__main__":
    serve()
