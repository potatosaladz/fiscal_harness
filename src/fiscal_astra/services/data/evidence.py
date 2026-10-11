"""Evidence verification and linkage service."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from fiscal_astra.database.enums import AccessStatus
from fiscal_astra.database.models_evidence import EvidenceClaim, EvidenceRecord
from fiscal_astra.services.data.errors import EvidenceVerificationError
from fiscal_astra.services.data.models import compute_checksum


class EvidenceService:
    """Persist, verify, and link evidence to claims with full provenance.

    Every evidence record must have a verified access status before it can be
    used to support a claim. Claims that depend on unverified evidence surface
    as failures in the deliberation layer so disagreements are visible.
    """

    def __init__(self, session: Session) -> None:
        self.session = session

    def record_evidence(
        self,
        citation: str,
        evidence_type: str,
        verification_status: str,
        *,
        url_or_doi: str | None = None,
        quote: str | None = None,
        finding: str | None = None,
        relevance: str | None = None,
        method_quality: str | None = None,
        limitations: list[Any] | None = None,
        applicability_to_scenario: str | None = None,
        provenance: dict[str, Any] | None = None,
        content_checksum_sha256: str | None = None,
        access_status: str = AccessStatus.NOT_CHECKED.value,
    ) -> uuid.UUID:
        """Persist an evidence record and return its identity.

        The evidence will be linked to claims and indexed by verification status
        so the deliberation layer can distinguish verified from unverified sources.
        """

        evidence_id = uuid.uuid4()
        evidence = EvidenceRecord(
            id=evidence_id,
            citation=citation,
            evidence_type=evidence_type,
            url_or_doi=url_or_doi,
            retrieved_at=datetime.now(UTC),
            quote=quote,
            finding=finding,
            relevance=relevance,
            method_quality=method_quality,
            limitations=limitations or [],
            applicability_to_scenario=applicability_to_scenario,
            verification_status=verification_status,
            provenance=provenance or {},
            content_checksum_sha256=content_checksum_sha256,
            access_status=access_status,
        )
        self.session.add(evidence)
        self.session.flush()
        return evidence_id

    def link_claim_to_evidence(
        self,
        evidence_id: uuid.UUID,
        claim_id: str,
        claim_text: str,
        claim_type: str,
        confidence: str,
    ) -> uuid.UUID:
        """Link a claim to evidence and verify the evidence is not contradicted.

        Raises EvidenceVerificationError if the evidence record cannot be found.
        """

        evidence = self.session.get(EvidenceRecord, evidence_id)
        if evidence is None:
            raise EvidenceVerificationError(
                f"evidence {evidence_id} not found", evidence_id=str(evidence_id)
            )
        if (
            evidence.verification_status != "verified"
            or evidence.access_status != AccessStatus.ACCESSIBLE.value
        ):
            raise EvidenceVerificationError(
                "evidence is not verified and accessible; it cannot support a factual claim",
                evidence_id=str(evidence_id),
            )

        link_id = uuid.uuid4()
        claim = EvidenceClaim(
            id=link_id,
            evidence_id=evidence_id,
            claim_id=claim_id,
            claim_text=claim_text,
            claim_type=claim_type,
            confidence=confidence,
        )
        self.session.add(claim)
        self.session.flush()
        return link_id

    def verify_evidence_access(
        self,
        evidence_id: uuid.UUID,
        payload: bytes | None = None,
    ) -> dict[str, Any]:
        """Verify that evidence is actually accessible.

        If payload is provided, compute and verify the checksum. Returns a
        dict describing the verification outcome for the audit trail.
        """

        evidence = self.session.get(EvidenceRecord, evidence_id)
        if evidence is None:
            return {
                "status": "not_found",
                "evidence_id": str(evidence_id),
                "access_status": AccessStatus.NOT_FOUND.value,
            }

        result = {
            "evidence_id": str(evidence_id),
            "citation": evidence.citation,
            "prior_access_status": evidence.access_status,
        }

        if payload is not None:
            checksum = compute_checksum(payload)
            if evidence.content_checksum_sha256 is not None:
                if checksum == evidence.content_checksum_sha256:
                    result["checksum_match"] = "match"
                    result["access_status"] = AccessStatus.ACCESSIBLE.value
                else:
                    result["checksum_match"] = "mismatch"
                    result["access_status"] = AccessStatus.REJECTED.value
                    result["message"] = "content checksum mismatch"
            else:
                evidence.content_checksum_sha256 = checksum
                result["checksum_stored"] = checksum
                result["access_status"] = AccessStatus.ACCESSIBLE.value
        else:
            result["access_status"] = AccessStatus.NOT_CHECKED.value

        evidence.access_status = result.get("access_status", AccessStatus.NOT_CHECKED.value)
        self.session.flush()
        return {
            "event": "evidence_verification",
            "at": datetime.now(UTC).isoformat(),
            **result,
        }

    def evidence_metadata_for_audit(self, evidence_id: uuid.UUID) -> dict[str, Any]:
        """Return evidence metadata for inclusion in audit trail."""

        evidence = self.session.get(EvidenceRecord, evidence_id)
        if evidence is None:
            return {"status": "not_found", "evidence_id": str(evidence_id)}

        claims = [
            {
                "claim_id": claim.claim_id,
                "claim_text": claim.claim_text,
                "claim_type": claim.claim_type,
                "confidence": claim.confidence,
            }
            for claim in evidence.claims
        ]

        return {
            "status": "found",
            "evidence_id": str(evidence.id),
            "citation": evidence.citation,
            "evidence_type": evidence.evidence_type,
            "url_or_doi": evidence.url_or_doi,
            "verification_status": evidence.verification_status,
            "access_status": evidence.access_status,
            "method_quality": evidence.method_quality,
            "retrieved_at": evidence.retrieved_at.isoformat() if evidence.retrieved_at else None,
            "claims": claims,
        }


__all__ = ["EvidenceService"]
