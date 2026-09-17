"""DRF endpoints for the RAG search feature.

Kept separate from the legacy function-based views in views.py so the DRF
conventions (APIView, serializers, IsAuthenticated) don't get tangled with the
csrf_exempt/JsonResponse patterns used elsewhere.
"""

import logging
from typing import cast

from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from note_generator.serializers import (
    NoteSearchRequestSerializer,
    NoteSearchResponseSerializer,
)

logger = logging.getLogger(__name__)


class NoteSearchView(APIView):
    """POST /api/notes/search/ -> 202 with a task id.

    The grading loop makes up to three retrievals and three grader calls, so it
    runs on a worker and the client polls NoteSearchStatusView. This is a
    breaking change from the old 200-with-answer response.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request, *args, **kwargs):
        req = NoteSearchRequestSerializer(data=request.data)
        req.is_valid(raise_exception=True)
        query = cast(dict, req.validated_data)["query"]

        try:
            from note_generator.tasks import search_notes_task

            task = search_notes_task.delay(request.user.id, query)
        except Exception as e:
            # Broker down. Enqueueing is the only thing that can fail here.
            logger.exception(
                f"Could not enqueue search for user {request.user.id}: {e}"
            )
            return Response(
                {
                    "error_code": "search_failed",
                    "message": "Search is temporarily unavailable.",
                },
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        return Response(
            {"task_id": task.id, "status": "processing"},
            status=status.HTTP_202_ACCEPTED,
        )


class NoteSearchStatusView(APIView):
    """GET /api/notes/search/<task_id>/ -> pending | processing | done | failed.

    Separate from views.task_status, whose payload is shaped around note_id.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, task_id, *args, **kwargs):
        from celery.result import AsyncResult

        result = AsyncResult(task_id)

        if result.state == "PENDING":
            return Response({"status": "pending"}, status=status.HTTP_200_OK)
        if result.state in ("STARTED", "PROGRESS"):
            return Response({"status": "processing"}, status=status.HTTP_200_OK)
        if result.state != "SUCCESS":
            logger.warning(f"search task {task_id} ended in state {result.state}")
            return Response(
                {"status": "failed", "message": "Search is temporarily unavailable."},
                status=status.HTTP_200_OK,
            )

        payload = result.result or {}
        if not isinstance(payload, dict) or payload.get("error"):
            message = "Search is temporarily unavailable."
            if isinstance(payload, dict) and payload.get("error"):
                message = payload["error"]
            return Response(
                {"status": "failed", "message": message}, status=status.HTTP_200_OK
            )

        body = NoteSearchResponseSerializer(
            {
                "answer": payload.get("answer") or "",
                "sources": payload.get("sources", []),
                "low_confidence": payload.get("low_confidence", False),
            }
        ).data
        return Response({"status": "done", **body}, status=status.HTTP_200_OK)
