"""Notion export helpers.

Calls the Notion REST API directly via `requests` so we don't pull in another SDK.
The interesting work is `text_to_notion_blocks`, which translates the plain-text
notes the AI generates into Notion's typed-block JSON format.
"""

from __future__ import annotations

import logging
from typing import Iterable

import requests

logger = logging.getLogger(__name__)

NOTION_API_URL = "https://api.notion.com/v1/pages"
NOTION_API_VERSION = "2022-06-28"

# Notion rejects rich-text content longer than 2000 chars per block.
NOTION_TEXT_LIMIT = 2000

# Notion accepts at most 100 child blocks in the initial page-create call.
NOTION_BLOCK_LIMIT = 100

# Section labels the AI prompt emits in ALL CAPS — rendered as headings on Notion.
HEADING_KEYWORDS = {
    "TL;DR",
    "KEY TERMS",
    "HOW IT WORKS",
    "STEP-BY-STEP",
    "QUICK CHECK QUIZ (with answers)",
    "QUICK CHECK QUIZ",
    "ANCHORS",
}


class NotionExportError(Exception):
    """Raised when the Notion API rejects the request."""


def _chunk_text(text: str, limit: int = NOTION_TEXT_LIMIT) -> Iterable[str]:
    """Yield slices of `text` no longer than `limit` characters."""
    for start in range(0, len(text), limit):
        yield text[start : start + limit]


def _rich_text(content: str) -> list[dict]:
    """Wrap a plain string in Notion's rich-text JSON shape, splitting if too long."""
    return [
        {"type": "text", "text": {"content": chunk}} for chunk in _chunk_text(content)
    ]


def _block(block_type: str, content: str) -> dict:
    """Build a single Notion block of the given type from a plain string."""
    return {
        "object": "block",
        "type": block_type,
        block_type: {"rich_text": _rich_text(content)},
    }


def text_to_notion_blocks(text: str) -> list[dict]:
    """Convert plain-text notes into a list of Notion block objects.

    Rules:
      - Lines that exactly match HEADING_KEYWORDS become heading_2 blocks.
      - Lines starting with "- " become bulleted_list_item blocks.
      - Numbered lines like "1) ..." become numbered_list_item blocks.
      - Blank lines are dropped (Notion handles spacing between blocks itself).
      - Everything else becomes a paragraph block.
    """
    blocks: list[dict] = []

    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            continue

        stripped = line.strip()

        if stripped in HEADING_KEYWORDS:
            blocks.append(_block("heading_2", stripped))
            continue

        if stripped.startswith("- "):
            blocks.append(_block("bulleted_list_item", stripped[2:].strip()))
            continue

        # Numbered list: "1) text" or "1. text"
        if (
            len(stripped) >= 3
            and stripped[0].isdigit()
            and stripped[1] in {")", "."}
            and stripped[2] == " "
        ):
            blocks.append(_block("numbered_list_item", stripped[3:].strip()))
            continue

        blocks.append(_block("paragraph", stripped))

    return blocks


def export_note_to_notion(
    *,
    token: str,
    parent_page_id: str,
    title: str,
    content: str,
    source_url: str = "",
) -> str:
    """Create a Notion page under `parent_page_id` and return its URL.

    Raises NotionExportError on any non-2xx response from Notion.
    """
    blocks = text_to_notion_blocks(content)

    if source_url:
        blocks.insert(0, _block("paragraph", f"Source: {source_url}"))

    # Notion caps initial children at 100. For long notes, we'd need to
    # follow up with PATCH /v1/blocks/{page_id}/children — for now we trim.
    if len(blocks) > NOTION_BLOCK_LIMIT:
        logger.warning(
            "Note has %d blocks, trimming to Notion's %d-block create limit",
            len(blocks),
            NOTION_BLOCK_LIMIT,
        )
        blocks = blocks[:NOTION_BLOCK_LIMIT]

    payload = {
        "parent": {"page_id": parent_page_id},
        "properties": {
            "title": {"title": _rich_text(title or "Untitled Note")},
        },
        "children": blocks,
    }

    headers = {
        "Authorization": f"Bearer {token}",
        "Notion-Version": NOTION_API_VERSION,
        "Content-Type": "application/json",
    }

    try:
        response = requests.post(
            NOTION_API_URL, json=payload, headers=headers, timeout=15
        )
    except requests.RequestException as e:
        raise NotionExportError(f"Network error contacting Notion: {e}") from e

    if response.status_code >= 400:
        # Notion returns useful JSON error messages; surface them when present.
        try:
            data = response.json()
            msg = data.get("message") or data.get("code") or response.text
        except ValueError:
            msg = response.text
        raise NotionExportError(f"Notion API error ({response.status_code}): {msg}")

    data = response.json()
    return data.get("url", "")
