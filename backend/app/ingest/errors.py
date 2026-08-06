"""Exceptions raised during ingest (ZIP/Git -> validated source/). The API
layer maps these to appropriate HTTP status codes / error bodies."""

from __future__ import annotations


class IngestError(Exception):
    """Base class for all ingest failures."""


class UploadTooLargeError(IngestError):
    pass


class ExtractedTooLargeError(IngestError):
    pass


class TooManyFilesError(IngestError):
    pass


class PathTraversalError(IngestError):
    pass


class GitCloneError(IngestError):
    pass


class NotMavenProjectError(IngestError):
    pass


class GradleProjectError(IngestError):
    """Detected a Gradle project -- explicitly out of scope per spec."""
