"""Typed failures for the data retrieval and evidence-provenance layer.

Every failure carries enough context to be written to the append-only audit
trail. Failures are never silently swallowed: a retrieval that did not succeed
must surface as an error event and, where relevant, as non-verified evidence.
"""

from __future__ import annotations


class RetrievalError(RuntimeError):
    """Base class for retrieval failures that must be audited."""

    access_status = "inaccessible"

    def __init__(self, message: str, *, origin: str | None = None) -> None:
        super().__init__(message)
        self.origin = origin

    def as_error_info(self) -> dict[str, object]:
        """Return a JSON-safe description for the audit trail."""

        return {
            "type": type(self).__name__,
            "message": str(self),
            "origin": self.origin,
            "access_status": self.access_status,
        }


class DatasetParseError(RetrievalError):
    """Raised when dataset bytes cannot be parsed into typed series."""

    access_status = "inaccessible"


class UnsupportedFormatError(DatasetParseError):
    """Raised when no adapter supports the requested dataset format."""

    access_status = "rejected"


class LocalPathNotAllowedError(RetrievalError):
    """Raised when a local path escapes the configured data root."""

    access_status = "rejected"


class UrlNotAllowlistedError(RetrievalError):
    """Raised when a remote origin is not on the configured allowlist."""

    access_status = "rejected"


class RemoteAccessError(RetrievalError):
    """Raised when an allowlisted remote origin could not be retrieved."""

    access_status = "inaccessible"

    def __init__(
        self,
        message: str,
        *,
        origin: str | None = None,
        access_status: str | None = None,
    ) -> None:
        super().__init__(message, origin=origin)
        if access_status is not None:
            self.access_status = access_status


class SnapshotConflictError(RuntimeError):
    """Raised when an immutable snapshot would have to be rewritten."""

    def __init__(self, message: str, *, snapshot_id: str | None = None) -> None:
        super().__init__(message)
        self.snapshot_id = snapshot_id

    def as_error_info(self) -> dict[str, object]:
        return {
            "type": type(self).__name__,
            "message": str(self),
            "snapshot_id": self.snapshot_id,
        }


class EvidenceVerificationError(RuntimeError):
    """Raised when a claim would rest on evidence that is not verified."""

    def __init__(self, message: str, *, evidence_id: str | None = None) -> None:
        super().__init__(message)
        self.evidence_id = evidence_id

    def as_error_info(self) -> dict[str, object]:
        return {
            "type": type(self).__name__,
            "message": str(self),
            "evidence_id": self.evidence_id,
        }
