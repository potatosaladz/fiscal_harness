"""Retrieval adapters for local files, uploads, APIs, and allowlisted URLs."""

from __future__ import annotations

import csv
import io
import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pyarrow.parquet as parquet  # type: ignore[import-untyped]

from fiscal_astra.config.settings import Settings
from fiscal_astra.services.data.errors import (
    DatasetParseError,
    LocalPathNotAllowedError,
    RemoteAccessError,
    UrlNotAllowlistedError,
)
from fiscal_astra.services.data.models import (
    RetrievalRequest,
    RetrievedDataset,
    RetrievedSeries,
    canonical_transformation,
    compute_checksum,
)
from fiscal_astra.services.data.parsers import infer_frequency, parse_tabular_rows


def _dataset_from_bytes(
    payload: bytes,
    *,
    media_type: str | None,
    dataset_name: str | None,
    parser: str,
) -> RetrievedDataset:
    """Parse raw bytes and attach a checksum and transformation provenance."""

    try:
        rows = _rows_from_bytes(payload, media_type=media_type, dataset_name=dataset_name)
        series = parse_tabular_rows(rows, dataset_name=dataset_name)
    except DatasetParseError:
        raise
    except Exception as error:
        raise DatasetParseError(f"failed to parse {parser} dataset: {error}") from error

    normalised = tuple(
        RetrievedSeries(
            code=item.code,
            name=item.name,
            observations=item.observations,
            unit=item.unit,
            frequency=infer_frequency(item),
            series_kind=item.series_kind,
            provenance_type=item.provenance_type,
            provenance=item.provenance,
        )
        for item in series
    )
    return RetrievedDataset(
        series=normalised,
        checksum_sha256=compute_checksum(payload),
        size_bytes=len(payload),
        source_metadata={"media_type": media_type, "parser": parser},
        transformation_history=(
            canonical_transformation("parse", parser=parser),
            canonical_transformation(
                "normalise", row_count=sum(len(s.observations) for s in series)
            ),
        ),
    )


def _rows_from_bytes(
    payload: bytes,
    *,
    media_type: str | None,
    dataset_name: str | None,
) -> list[dict[str, Any]]:
    media = (media_type or "").lower().split(";", 1)[0].strip()
    name = (dataset_name or "").lower()
    if media in {"application/parquet", "application/x-parquet"} or name.endswith(".parquet"):
        table = parquet.read_table(io.BytesIO(payload))
        return [dict(row) for row in table.to_pylist()]
    if media in {"application/json", "text/json"} or name.endswith(".json"):
        decoded = json.loads(payload.decode("utf-8"))
        if isinstance(decoded, dict):
            decoded = decoded.get("data", decoded.get("rows", []))
        if not isinstance(decoded, list) or not all(isinstance(item, dict) for item in decoded):
            raise DatasetParseError("JSON dataset must contain a list of objects")
        return [dict(item) for item in decoded]

    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise DatasetParseError("dataset is not UTF-8 CSV or JSON") from error
    return [dict(row) for row in csv.DictReader(io.StringIO(text))]


@dataclass(slots=True)
class CsvAdapter:
    """Read a CSV file under the configured data root."""

    settings: Settings
    provider: str = "csv"
    media_types: tuple[str, ...] = ("text/csv", "text/plain")

    def supports(self, request: RetrievalRequest) -> bool:
        return request.provider == self.provider

    def retrieve(self, request: RetrievalRequest) -> RetrievedDataset:
        path = _safe_local_path(request.location, self.settings)
        try:
            payload = path.read_bytes()
        except OSError as error:
            raise DatasetParseError(
                f"could not read CSV {path}: {error}", origin=str(path)
            ) from error
        return _dataset_from_bytes(
            payload,
            media_type=request.media_type or "text/csv",
            dataset_name=request.dataset_name or path.name,
            parser="csv",
        )


@dataclass(slots=True)
class ParquetAdapter:
    """Read a Parquet file under the configured data root."""

    settings: Settings
    provider: str = "parquet"
    media_types: tuple[str, ...] = ("application/parquet", "application/x-parquet")

    def supports(self, request: RetrievalRequest) -> bool:
        return request.provider == self.provider

    def retrieve(self, request: RetrievalRequest) -> RetrievedDataset:
        path = _safe_local_path(request.location, self.settings)
        try:
            payload = path.read_bytes()
        except OSError as error:
            raise DatasetParseError(
                f"could not read Parquet {path}: {error}", origin=str(path)
            ) from error
        return _dataset_from_bytes(
            payload,
            media_type=request.media_type or "application/parquet",
            dataset_name=request.dataset_name or path.name,
            parser="parquet",
        )


@dataclass(slots=True)
class UploadAdapter:
    """Parse caller-supplied bytes without treating them as trusted evidence."""

    provider: str = "upload"
    media_types: tuple[str, ...] = ("text/csv", "application/parquet", "application/json")

    def supports(self, request: RetrievalRequest) -> bool:
        return request.provider == self.provider

    def retrieve(self, request: RetrievalRequest) -> RetrievedDataset:
        payload = request.parameters.get("payload")
        if not isinstance(payload, bytes):
            raise DatasetParseError("upload request requires bytes in parameters['payload']")
        return _dataset_from_bytes(
            payload,
            media_type=request.media_type,
            dataset_name=request.dataset_name,
            parser="upload",
        )


@dataclass(slots=True)
class ApiAdapter:
    """Parse an API response supplied by an API client boundary.

    The adapter does not perform HTTP itself. The caller supplies the response
    bytes and source metadata, allowing authentication, retries, and provider
    SDKs to remain outside the persistence service while retaining provenance.
    """

    provider: str = "api"
    media_types: tuple[str, ...] = ("application/json", "text/csv", "application/parquet")

    def supports(self, request: RetrievalRequest) -> bool:
        return request.provider == self.provider

    def retrieve(self, request: RetrievalRequest) -> RetrievedDataset:
        payload = request.parameters.get("payload")
        if not isinstance(payload, bytes):
            raise DatasetParseError("API request requires response bytes in parameters['payload']")
        result = _dataset_from_bytes(
            payload,
            media_type=request.media_type or "application/json",
            dataset_name=request.dataset_name or request.location,
            parser="api",
        )
        return RetrievedDataset(
            series=result.series,
            checksum_sha256=result.checksum_sha256,
            size_bytes=result.size_bytes,
            source_metadata={**result.source_metadata, "api_endpoint": request.location},
            transformation_history=result.transformation_history,
            retrieved_at=result.retrieved_at,
        )


@dataclass(slots=True)
class AllowlistedUrlAdapter:
    """Fetch and parse bytes only from explicitly configured hostnames."""

    settings: Settings
    provider: str = "url"
    media_types: tuple[str, ...] = ("text/csv", "application/json", "application/parquet")

    def supports(self, request: RetrievalRequest) -> bool:
        return request.provider == self.provider or request.location.startswith(
            ("http://", "https://")
        )

    def retrieve(self, request: RetrievalRequest) -> RetrievedDataset:
        _validate_url(request.location, self.settings)
        parsed = urllib.parse.urlparse(request.location)
        request_obj = urllib.request.Request(
            request.location,
            headers={"User-Agent": "FiscalAstra-Retrieval/1.0"},
        )
        try:
            with urllib.request.urlopen(
                request_obj, timeout=self.settings.retrieval_request_timeout_seconds
            ) as response:
                payload = response.read()
                media_type = request.media_type or response.headers.get_content_type()
        except urllib.error.HTTPError as error:
            status = (
                "not_found"
                if error.code == 404
                else "forbidden"
                if error.code in {401, 403}
                else "inaccessible"
            )
            raise RemoteAccessError(
                f"URL returned HTTP {error.code}",
                origin=request.location,
                access_status=status,
            ) from error
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise RemoteAccessError(
                f"URL retrieval failed: {error}", origin=request.location
            ) from error
        result = _dataset_from_bytes(
            payload,
            media_type=media_type,
            dataset_name=request.dataset_name or Path(parsed.path).name or parsed.netloc,
            parser="allowlisted_url",
        )
        return RetrievedDataset(
            series=result.series,
            checksum_sha256=result.checksum_sha256,
            size_bytes=result.size_bytes,
            source_metadata={
                **result.source_metadata,
                "url": request.location,
                "http_status": 200,
            },
            transformation_history=result.transformation_history,
            retrieved_at=result.retrieved_at,
        )


def _safe_local_path(location: str, settings: Settings) -> Path:
    root = Path(settings.data_root).expanduser().resolve()
    candidate = Path(location).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as error:
        raise LocalPathNotAllowedError(
            f"path {location!r} is outside configured data root", origin=location
        ) from error
    return resolved


def _validate_url(location: str, settings: Settings) -> None:
    parsed = urllib.parse.urlparse(location)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise UrlNotAllowlistedError(f"unsupported URL origin: {location}", origin=location)
    hostname = parsed.hostname.lower().rstrip(".")
    allowed = {item.lower().rstrip(".") for item in settings.retrieval_url_allowlist}
    if hostname not in allowed:
        raise UrlNotAllowlistedError(
            f"URL hostname {hostname!r} is not allowlisted", origin=location
        )


__all__ = [
    "AllowlistedUrlAdapter",
    "ApiAdapter",
    "CsvAdapter",
    "ParquetAdapter",
    "UploadAdapter",
]
