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
    """POST /api/notes/search/  -> RAG-answered question over the user's notes."""

    permission_classes = [IsAuthenticated]

    def post(self, request, *args, **kwargs):
        req = NoteSearchRequestSerializer(data=request.data)
        req.is_valid(raise_exception=True)
        query = cast(dict, req.validated_data)["query"]

        try:
            # Lazy import: keeps LangChain off the URL-loading path so manage.py
            # commands work without the RAG deps installed.
            from note_generator.rag.chain import build_user_chain

            chain = build_user_chain(user_id=request.user.id)
            result = chain.invoke({"question": query})
        except Exception as e:
            logger.exception(f"RAG chain failed for user {request.user.id}: {e}")
            return Response(
                {
                    "error_code": "search_failed",
                    "message": "Search is temporarily unavailable.",
                },
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        sources = [
            {
                "note_id": d.metadata.get("note_id"),
                "title": d.metadata.get("title", ""),
                "source": d.metadata.get("source", ""),
            }
            for d in result.get("docs", [])
        ]

        payload = NoteSearchResponseSerializer(
            {"answer": result["answer"], "sources": sources}
        ).data
        return Response(payload, status=status.HTTP_200_OK)
