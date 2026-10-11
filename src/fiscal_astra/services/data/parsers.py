"""Shared parsing helpers for tabular provider payloads."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from fiscal_astra.database.enums import SeriesFrequency
from fiscal_astra.services.data.errors import DatasetParseError
from fiscal_astra.services.data.models import RetrievedObservation, RetrievedSeries

PERIOD_COLUMNS = ("period", "date", "year", "quarter", "month")
VALUE_COLUMNS = ("value", "amount", "observation", "level")
SERIES_COLUMNS = ("series", "series_code", "series_id", "code", "indicator")
UNIT_COLUMNS = ("unit", "units", "currency")
FREQUENCY_COLUMNS = ("frequency", "freq")


def _first_present(row: Mapping[str, Any], candidates: Sequence[str]) -> str | None:
    lowered = {str(key).strip().lower(): key for key in row}
    for candidate in candidates:
        key = lowered.get(candidate)
        if key is not None:
            return str(key)
    return None


def _coerce_float(value: Any) -> float:
    if isinstance(value, bool):
        raise DatasetParseError(f"boolean value is not a valid observation: {value!r}")
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        raise DatasetParseError("empty observation value")
    cleaned = text.replace("_", "").replace(",", "")
    try:
        return float(cleaned)
    except ValueError as error:
        raise DatasetParseError(f"observation value is not numeric: {value!r}") from error


def parse_tabular_rows(
    rows: Iterable[Mapping[str, Any]],
    *,
    dataset_name: str | None,
    default_unit: str | None = None,
    default_frequency: SeriesFrequency | str | None = None,
) -> tuple[RetrievedSeries, ...]:
    """Group long-format rows into series with typed observations.

    Two shapes are accepted:

    * a ``series``/``code`` column distinguishes multiple series;
    * a wide layout with one ``period`` column plus numeric value columns, where
      every non-period column becomes its own series.

    Rows are never dropped silently: a row with an unparseable value raises
    :class:`DatasetParseError` so the failure reaches the audit trail instead of
    quietly shrinking the dataset.
    """

    materialised = [dict(row) for row in rows]
    if not materialised:
        raise DatasetParseError("dataset contains no rows")

    columns = list(materialised[0].keys())
    period_column = _first_present(materialised[0], PERIOD_COLUMNS)
    series_column = _first_present(materialised[0], SERIES_COLUMNS)
    value_column = _first_present(materialised[0], VALUE_COLUMNS)
    unit_column = _first_present(materialised[0], UNIT_COLUMNS)
    frequency_column = _first_present(materialised[0], FREQUENCY_COLUMNS)

    if period_column is None:
        raise DatasetParseError("dataset has no recognisable period column")

    grouped: OrderedDict[str, list[RetrievedObservation]] = OrderedDict()
    units: dict[str, str | None] = {}
    frequencies: dict[str, str | None] = {}

    if series_column is not None and value_column is not None:
        for row in materialised:
            code = str(row.get(series_column, dataset_name or "series")).strip() or "series"
            period = str(row[period_column]).strip()
            grouped.setdefault(code, []).append(
                RetrievedObservation(period=period, value=_coerce_float(row[value_column]))
            )
            if unit_column is not None and code not in units:
                raw_unit = row.get(unit_column)
                units[code] = str(raw_unit) if raw_unit not in (None, "") else default_unit
            if frequency_column is not None and code not in frequencies:
                raw_frequency = row.get(frequency_column)
                frequencies[code] = (
                    str(raw_frequency)
                    if raw_frequency not in (None, "")
                    else _as_text(default_frequency)
                )
    else:
        value_columns = [column for column in columns if column != period_column]
        numeric_columns = [
            column for column in value_columns if not column.lower().endswith(("unit", "frequency"))
        ]
        if not numeric_columns:
            raise DatasetParseError("dataset has no value column")
        for row in materialised:
            period = str(row[period_column]).strip()
            for column in numeric_columns:
                raw_value = row.get(column)
                if raw_value in (None, ""):
                    continue
                grouped.setdefault(column, []).append(
                    RetrievedObservation(period=period, value=_coerce_float(raw_value))
                )

    if not grouped:
        raise DatasetParseError("dataset produced no observations")

    series: list[RetrievedSeries] = []
    for code, observations in grouped.items():
        series.append(
            RetrievedSeries(
                code=code,
                name=code,
                observations=tuple(observations),
                unit=units.get(code, default_unit),
                frequency=_coerce_frequency(frequencies.get(code) or default_frequency),
                provenance={
                    "dataset_name": dataset_name,
                    "row_count": len(observations),
                },
            )
        )
    return tuple(series)


def _as_text(value: SeriesFrequency | str | None) -> str | None:
    if value is None:
        return None
    return str(value)


def _coerce_frequency(
    value: SeriesFrequency | str | None,
) -> SeriesFrequency | str | None:
    if value is None or isinstance(value, SeriesFrequency):
        return value
    text = str(value).strip().lower()
    if not text:
        return None
    try:
        return SeriesFrequency(text)
    except ValueError:
        return text


def infer_frequency(series: RetrievedSeries) -> SeriesFrequency | str | None:
    """Infer a coarse frequency from observation spacing when unspecified."""

    if series.frequency is not None:
        return series.frequency
    periods = [observation.period for observation in series.observations]
    if not periods:
        return None
    sample = periods[0]
    if len(sample) == 4 and sample.isdigit():
        return SeriesFrequency.ANNUAL
    if "Q" in sample.upper():
        return SeriesFrequency.QUARTERLY
    return None


__all__ = ["infer_frequency", "parse_tabular_rows"]
