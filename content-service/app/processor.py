from __future__ import annotations

from dataclasses import dataclass
from typing import List


@dataclass
class ProcessedNotes:
    summary: str
    chunk_count: int
    sections: list[dict]


def chunk_text(text: str, words_per_chunk: int = 180) -> List[str]:
    words = text.split()
    if not words:
        return []

    chunks = []
    for i in range(0, len(words), words_per_chunk):
        chunks.append(" ".join(words[i : i + words_per_chunk]))
    return chunks


def _sentence_split(text: str) -> List[str]:
    parts = text.replace("\n", " ").split(".")
    return [p.strip() for p in parts if p.strip()]


def process_transcript(transcript_text: str, max_sections: int = 5) -> ProcessedNotes:
    clean_text = (transcript_text or "").strip()
    if not clean_text:
        raise ValueError("transcript_text is empty")

    chunks = chunk_text(clean_text)
    sentences = _sentence_split(clean_text)

    # Minimal summarization strategy: keep first 3 meaningful sentences.
    summary_parts = sentences[:3] if sentences else [clean_text[:320]]
    summary = ". ".join(summary_parts).strip()
    if summary and not summary.endswith("."):
        summary += "."

    sections = [
        {
            "heading": "Summary",
            "bullets": [summary],
        },
        {
            "heading": "Key Points",
            "bullets": [s + "." for s in sentences[3 : 3 + max_sections] if s],
        },
        {
            "heading": "Chunk Stats",
            "bullets": [
                f"Total transcript words: {len(clean_text.split())}",
                f"Chunk size: 180 words",
                f"Chunk count: {len(chunks)}",
            ],
        },
    ]

    return ProcessedNotes(summary=summary, chunk_count=len(chunks), sections=sections)
