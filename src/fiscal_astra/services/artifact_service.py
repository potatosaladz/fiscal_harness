"""Service for recording S3-compatible object-storage artifact references.

The service never uploads bytes directly — it records the bucket/key and
checksum that an object-storage worker produced, which keeps object-storage
credentials out of the application process.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from fiscal_astra.database.models_simulation import SimulationArtifact


class ArtifactService:
    """Register artifact references; bytes remain in object storage."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def register_artifact(
        self,
        run_id: uuid.UUID,
        artifact_kind: str,
        bucket: str,
        object_key: str,
        content_type: str | None = None,
        size_bytes: int | None = None,
        checksum_sha256: str | None = None,
        description: str | None = None,
        model_id: uuid.UUID | None = None,
        result_id: uuid.UUID | None = None,
    ) -> SimulationArtifact:
        artifact = SimulationArtifact(
            run_id=run_id,
            model_id=model_id,
            result_id=result_id,
            artifact_kind=artifact_kind,
            bucket=bucket,
            object_key=object_key,
            content_type=content_type,
            size_bytes=size_bytes,
            checksum_sha256=checksum_sha256,
            description=description,
        )
        self._session.add(artifact)
        self._session.flush()
        return artifact

    def get_artifact(self, artifact_id: uuid.UUID) -> SimulationArtifact | None:
        return self._session.get(SimulationArtifact, artifact_id)

    def find_by_object(self, bucket: str, object_key: str) -> SimulationArtifact | None:
        stmt = select(SimulationArtifact).where(
            SimulationArtifact.bucket == bucket,
            SimulationArtifact.object_key == object_key,
        )
        return self._session.execute(stmt).scalar_one_or_none()

    @staticmethod
    def s3_url(bucket: str, object_key: str, endpoint: str | None = None) -> str:
        """Render a human-readable S3 URI for an artifact.

        Only constructs a URI from parameters already provided; does not probe
        or authenticate against object storage.
        """

        if endpoint:
            return f"{endpoint}/{bucket}/{object_key}"
        return f"s3://{bucket}/{object_key}"
