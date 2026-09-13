"""Free YouTube transcripts via youtube-transcript-api, behind a protocol so tests inject a fake."""
from __future__ import annotations

import re
from typing import Protocol

_VIDEO_ID = re.compile(r"(?:v=|/shorts/|youtu\.be/|/embed/)([A-Za-z0-9_-]{6,})")


class TranscriptProvider(Protocol):
    def fetch(self, video_id: str) -> str | None: ...


class YouTubeTranscriptProvider:
    def fetch(self, video_id: str) -> str | None:
        from youtube_transcript_api import YouTubeTranscriptApi

        try:
            api = YouTubeTranscriptApi()
            transcript = api.fetch(video_id)
            return " ".join(snippet.text for snippet in transcript).strip() or None
        except Exception:
            return None


class FakeTranscriptProvider:
    def __init__(self, transcripts: dict[str, str] | None = None, fail: bool = False) -> None:
        self.transcripts = transcripts or {}
        self.fail = fail
        self.calls: list[str] = []

    def fetch(self, video_id: str) -> str | None:
        self.calls.append(video_id)
        if self.fail:
            raise RuntimeError("transcript unavailable")
        return self.transcripts.get(video_id)


def youtube_video_id(url: str | None, provider_id: str | None = None) -> str | None:
    if url:
        match = _VIDEO_ID.search(url)
        if match:
            return match.group(1)
    return provider_id
