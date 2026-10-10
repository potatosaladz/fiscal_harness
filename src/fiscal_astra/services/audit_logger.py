"""Transactional, append-only audit logging with hash-chain verification."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from fiscal_astra.database.enums import AuditActionType
from fiscal_astra.database.models_evidence import AuditEvent
from fiscal_astra.database.models_simulation import SimulationRun

_SENSITIVE_KEY = re.compile(
    r"(?:password|passwd|secret|api[_-]?key|access[_-]?key|secret[_-]?key|token|credential|authorization|private[_-]?key)",
    re.IGNORECASE,
)
_SENSITIVE_TEXT = re.compile(
    r"(?i)(?:bearer\s+|api[_-]?key\s*[=:]\s*|password\s*[=:]\s*|secret\s*[=:]\s*|token\s*[=:]\s*)[^\s,;]+"
)
_REDACTED = "[REDACTED]"


def redact_sensitive(value: Any) -> Any:
    """Return JSON-safe audit data with credential-like fields redacted."""

    if isinstance(value, Mapping):
        return {
            str(key): _REDACTED if _SENSITIVE_KEY.search(str(key)) else redact_sensitive(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple, set)):
        return [redact_sensitive(item) for item in value]
    if isinstance(value, str):
        return _SENSITIVE_TEXT.sub(_REDACTED, value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _canonical_payload(event: AuditEvent) -> str:
    """Serialize the immutable event fields used by the tamper-evident hash."""

    payload = {
        "run_id": str(event.run_id),
        "sequence": event.sequence,
        "trace_id": event.trace_id,
        "timestamp": event.timestamp.astimezone(UTC).isoformat(),
        "actor_type": event.actor_type,
        "actor_id": event.actor_id,
        "action_type": str(event.action_type),
        "description": event.description,
        "input_refs": event.input_refs,
        "output_refs": event.output_refs,
        "assumption_refs": event.assumption_refs,
        "evidence_refs": event.evidence_refs,
        "confidence": event.confidence,
        "error_info": event.error_info,
        "previous_event_hash": event.previous_event_hash,
        "next_action": event.next_action,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def calculate_event_hash(event: AuditEvent) -> str:
    """Calculate the SHA-256 hash for an event and its predecessor hash."""

    return hashlib.sha256(_canonical_payload(event).encode("utf-8")).hexdigest()


class AuditLogger:
    """Write and inspect audit events using an independent transaction.

    The logger deliberately owns a session per operation. Consequently an error
    event remains committed even if the caller later rolls back its domain
    transaction. Credentials are recursively redacted before persistence.
    """

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def record(
        self,
        *,
        run_id: uuid.UUID,
        trace_id: str,
        actor_type: str,
        actor_id: str,
        action_type: AuditActionType | str,
        description: str,
        input_refs: Iterable[Any] = (),
        output_refs: Iterable[Any] = (),
        assumption_refs: Iterable[Any] = (),
        evidence_refs: Iterable[Any] = (),
        confidence: str | None = None,
        error_info: Iterable[Any] = (),
        next_action: str | None = None,
        timestamp: datetime | None = None,
    ) -> AuditEvent:
        """Append one event and commit it atomically."""

        with self._session_factory() as session, session.begin():
            event = self._append(
                session,
                run_id=run_id,
                trace_id=trace_id,
                actor_type=actor_type,
                actor_id=actor_id,
                action_type=action_type,
                description=description,
                input_refs=input_refs,
                output_refs=output_refs,
                assumption_refs=assumption_refs,
                evidence_refs=evidence_refs,
                confidence=confidence,
                error_info=error_info,
                next_action=next_action,
                timestamp=timestamp,
            )
            session.expunge(event)
            return event

    def record_error(
        self,
        *,
        run_id: uuid.UUID,
        trace_id: str,
        actor_type: str,
        actor_id: str,
        description: str,
        error_info: Iterable[Any],
        next_action: str | None = None,
    ) -> AuditEvent:
        """Persist a failure event in its own transaction."""

        return self.record(
            run_id=run_id,
            trace_id=trace_id,
            actor_type=actor_type,
            actor_id=actor_id,
            action_type=AuditActionType.ERROR,
            description=description,
            error_info=error_info,
            next_action=next_action,
        )

    def list_for_run(self, run_id: uuid.UUID) -> list[AuditEvent]:
        """Return a run's events in chain order."""

        with self._session_factory() as session:
            statement = (
                select(AuditEvent).where(AuditEvent.run_id == run_id).order_by(AuditEvent.sequence)
            )
            return list(session.scalars(statement).all())

    def export_json(self, run_id: uuid.UUID) -> str:
        """Export a run's audit events as a JSON array."""

        return json.dumps(
            [self._event_dict(event) for event in self.list_for_run(run_id)], indent=2
        )

    def verify_chain(self, run_id: uuid.UUID) -> bool:
        """Verify sequence continuity, predecessor links, and event hashes."""

        events = self.list_for_run(run_id)
        previous_hash = ""
        for expected_sequence, event in enumerate(events, start=1):
            if event.sequence != expected_sequence or event.previous_event_hash != previous_hash:
                return False
            if calculate_event_hash(event) != event.current_event_hash:
                return False
            previous_hash = event.current_event_hash
        return True

    def _append(self, session: Session, **kwargs: Any) -> AuditEvent:
        run_statement = (
            select(SimulationRun).where(SimulationRun.id == kwargs["run_id"]).with_for_update()
        )
        if session.scalars(run_statement).first() is None:
            raise ValueError(f"SimulationRun not found: {kwargs['run_id']}")
        last_statement = (
            select(AuditEvent)
            .where(AuditEvent.run_id == kwargs["run_id"])
            .order_by(AuditEvent.sequence.desc())
            .with_for_update()
            .limit(1)
        )
        last = session.scalars(last_statement).first()
        event = AuditEvent(
            run_id=kwargs["run_id"],
            sequence=(last.sequence + 1) if last else 1,
            trace_id=kwargs["trace_id"],
            timestamp=kwargs["timestamp"] or datetime.now(UTC),
            actor_type=str(redact_sensitive(kwargs["actor_type"])),
            actor_id=str(redact_sensitive(kwargs["actor_id"])),
            action_type=kwargs["action_type"],
            description=str(redact_sensitive(kwargs["description"])),
            input_refs=redact_sensitive(list(kwargs["input_refs"])),
            output_refs=redact_sensitive(list(kwargs["output_refs"])),
            assumption_refs=redact_sensitive(list(kwargs["assumption_refs"])),
            evidence_refs=redact_sensitive(list(kwargs["evidence_refs"])),
            confidence=kwargs["confidence"],
            error_info=redact_sensitive(list(kwargs["error_info"])),
            previous_event_hash=last.current_event_hash if last else "",
            next_action=(
                str(redact_sensitive(kwargs["next_action"]))
                if kwargs["next_action"] is not None
                else None
            ),
        )
        event.current_event_hash = calculate_event_hash(event)
        session.add(event)
        session.flush()
        return event

    @staticmethod
    def _event_dict(event: AuditEvent) -> dict[str, Any]:
        return {
            "id": str(event.id),
            "run_id": str(event.run_id),
            "sequence": event.sequence,
            "trace_id": event.trace_id,
            "timestamp": event.timestamp.isoformat(),
            "actor_type": event.actor_type,
            "actor_id": event.actor_id,
            "action_type": str(event.action_type),
            "description": event.description,
            "input_refs": event.input_refs,
            "output_refs": event.output_refs,
            "assumption_refs": event.assumption_refs,
            "evidence_refs": event.evidence_refs,
            "confidence": event.confidence,
            "error_info": event.error_info,
            "previous_event_hash": event.previous_event_hash,
            "current_event_hash": event.current_event_hash,
            "next_action": event.next_action,
        }
