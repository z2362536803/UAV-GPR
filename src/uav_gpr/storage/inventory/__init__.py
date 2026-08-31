"""Air-ground mission inventory and per-trace consistency service (ISSUE-014).

Pure application/storage consistency service: it turns two ISSUE-011
``RcScanReader`` instances (air-side file, ground-side file) into a paged,
streaming ``MissionInventory`` that

- checks the mission-level contract (``mission_id``, channels, frequency
  axis, mission config digest) and reports mismatches without blocking the
  trace comparison;
- classifies every ``trace_index`` as ``missing`` (air only), ``extra``
  (ground only), ``consistent`` (index + uid + canonical raw hash agree),
  ``conflict`` (same index with a different ``trace_uid`` or different
  ``raw_trace_sha256``; or an index excluded from one side's logical view by
  an intra-file conflict) or ``gnss_diff`` (raw identity intact, GNSS
  differs);
- never compares whole-file hashes and never compares raw arrays: the
  per-trace canonical raw hash (DATA_FORMAT §5, ISSUE-009 framing) is the
  single identity criterion; GNSS differences are reported separately
  (DATA_FORMAT §5: GNSS never enters the raw hash);
- never treats ground-only processed/transport groups as a raw inconsistency
  (DATA_FORMAT §6);
- streams both sides in logical (``trace_index``) order with bounded memory
  (no raw arrays retained; pages replay deterministically) and serializes a
  stable report (``REPORT_FORMAT``/``REPORT_VERSION``) for protocol
  (inventory_summary/missing_request/conflict_report/sync_complete) and
  diagnostic reuse (TRANSPORT_PROTOCOL §4/§8).

This module performs no network I/O, no retransmission and no file
modification.  It does not change the public semantics of any existing
module.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from enum import StrEnum
from typing import cast

import numpy as np

from uav_gpr.core.errors import DomainError, ErrorCode, JsonValue
from uav_gpr.core.gnss import GnssMatch
from uav_gpr.storage.rcscan_reader import RcScanReader, ReadTrace

REPORT_FORMAT = "uav_gpr_air_ground_inventory"
REPORT_VERSION = 1

_CHUNK_ROWS = 64


class InventoryItemKind(StrEnum):
    """Machine-readable kind of one per-index inventory classification."""

    MISSING = "missing"
    EXTRA = "extra"
    CONSISTENT = "consistent"
    CONFLICT = "conflict"
    GNSS_DIFF = "gnss_diff"


@dataclass(frozen=True, slots=True)
class InventoryItem:
    """One classified ``trace_index`` (stable, JSON-safe)."""

    kind: InventoryItemKind
    trace_index: int
    air_trace_uid: str | None
    ground_trace_uid: str | None
    air_raw_sha256: str | None
    ground_raw_sha256: str | None
    detail: str | None

    def to_dict(self) -> dict[str, object]:
        """Plain JSON-safe serialization with stable key order."""
        return {
            "kind": self.kind.value,
            "trace_index": self.trace_index,
            "air_trace_uid": self.air_trace_uid,
            "ground_trace_uid": self.ground_trace_uid,
            "air_raw_sha256": self.air_raw_sha256,
            "ground_raw_sha256": self.ground_raw_sha256,
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class InventorySummary:
    """Cross-side counts plus per-side reader-level quality counts."""

    air_traces: int
    ground_traces: int
    matched: int
    missing: int
    extra: int
    conflicts: int
    gnss_diffs: int
    air_duplicates: int
    ground_duplicates: int
    air_conflicts: int
    ground_conflicts: int
    air_issues: int
    ground_issues: int

    def to_dict(self) -> dict[str, object]:
        return {
            "air_traces": self.air_traces,
            "ground_traces": self.ground_traces,
            "matched": self.matched,
            "missing": self.missing,
            "extra": self.extra,
            "conflicts": self.conflicts,
            "gnss_diffs": self.gnss_diffs,
            "air_duplicates": self.air_duplicates,
            "ground_duplicates": self.ground_duplicates,
            "air_conflicts": self.air_conflicts,
            "ground_conflicts": self.ground_conflicts,
            "air_issues": self.air_issues,
            "ground_issues": self.ground_issues,
        }


@dataclass(frozen=True, slots=True)
class ContractIssue:
    """One mismatched mission-level contract field."""

    field: str
    match: bool
    air: str | None
    ground: str | None
    detail: str | None

    def to_dict(self) -> dict[str, object]:
        return {
            "field": self.field,
            "match": self.match,
            "air": self.air,
            "ground": self.ground,
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class ContractReport:
    """Mission-level contract comparison (report-only, never blocks)."""

    mission_id_match: bool
    channels_match: bool
    frequencies_match: bool
    config_match: bool
    issues: tuple[ContractIssue, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "mission_id_match": self.mission_id_match,
            "channels_match": self.channels_match,
            "frequencies_match": self.frequencies_match,
            "config_match": self.config_match,
            "issues": [issue.to_dict() for issue in self.issues],
        }


@dataclass(frozen=True, slots=True)
class InventoryPage:
    """One bounded page of the deterministic item stream."""

    page_index: int
    page_size: int
    has_more: bool
    items: tuple[InventoryItem, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "page_index": self.page_index,
            "page_size": self.page_size,
            "has_more": self.has_more,
            "item_count": len(self.items),
            "items": [item.to_dict() for item in self.items],
        }


@dataclass(frozen=True, slots=True)
class _SideSets:
    """Per-side distinct committed indices and logical-view exclusions."""

    air_distinct: frozenset[int]
    air_excluded: frozenset[int]
    ground_distinct: frozenset[int]
    ground_excluded: frozenset[int]


def _require_page_size(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise DomainError(
            ErrorCode.INVALID_ARGUMENT,
            "page_size must be a positive integer",
            {"page_size": cast(JsonValue, value)},
        )
    return value


def _require_page_index(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise DomainError(
            ErrorCode.INVALID_ARGUMENT,
            "page_index must be a non-negative integer",
            {"page_index": cast(JsonValue, value)},
        )
    return value


def _require_kind(value: object) -> InventoryItemKind | None:
    if value is None:
        return None
    if not isinstance(value, InventoryItemKind):
        raise DomainError(
            ErrorCode.INVALID_ARGUMENT,
            "kind must be an InventoryItemKind or None",
            {"kind": cast(JsonValue, value)},
        )
    return value


def _gnss_diff_detail(air: GnssMatch | None, ground: GnssMatch | None) -> str | None:
    """Describe the GNSS difference of a raw-consistent pair, or ``None``.

    GNSS never participates in the raw hash (DATA_FORMAT §5), so a GNSS
    difference is reported separately and never becomes a raw conflict.
    """
    if air is None and ground is None:
        return None
    if air is None:
        return "air has no GNSS match; ground has one"
    if ground is None:
        return "ground has no GNSS match; air has one"
    air_dict = air.to_dict()
    ground_dict = ground.to_dict()
    if air_dict == ground_dict:
        return None
    fields: list[str] = []
    for field in (
        "fix",
        "trace_midpoint_utc",
        "age_s",
        "method",
        "usable_for_map",
        "reason",
    ):
        if air_dict[field] != ground_dict[field]:
            fields.append(field)
    return "differing GNSS fields: " + ", ".join(fields)


def _axis_summary(axis: np.ndarray) -> str:
    if axis.size == 0:
        return "empty axis"
    return f"{axis.size} points [{axis[0]:.9g}..{axis[-1]:.9g}] Hz"


def _side_index_sets(reader: RcScanReader) -> tuple[frozenset[int], frozenset[int]]:
    """Return (distinct committed indices, indices excluded from the logical
    view) for one side.

    The logical view collapses duplicate-same copies and excludes
    intra-file conflicting identity groups (ISSUE-011); excluded indices are
    the committed indices that the logical view does not serve.  Only small
    integer sets are materialized — never raw arrays.  When the reader
    reports no intra-file conflicts (the common case) the physical pass is
    skipped entirely.
    """
    report = reader.validation_report()
    logical: set[int] = set()
    for chunk in reader.iter_logical(chunk_rows=_CHUNK_ROWS):
        for record in chunk.records:
            logical.add(record.trace_index)
    if not report.conflicts:
        return frozenset(logical), frozenset()
    physical: set[int] = set()
    for chunk in reader.iter_physical(chunk_rows=_CHUNK_ROWS):
        for record in chunk.records:
            physical.add(record.trace_index)
    return frozenset(physical), frozenset(physical - logical)


class _RecordStream:
    """One-record lookahead over a reader's logical view (chunked, lazy)."""

    __slots__ = ("_pending", "_records")

    def __init__(self, reader: RcScanReader) -> None:
        self._records: Iterator[ReadTrace] = (
            record
            for chunk in reader.iter_logical(chunk_rows=_CHUNK_ROWS)
            for record in chunk.records
        )
        self._pending = self._pull()

    def _pull(self) -> ReadTrace | None:
        try:
            return next(self._records)
        except StopIteration:
            return None

    def peek(self) -> ReadTrace | None:
        return self._pending

    def pop(self) -> ReadTrace:
        record = self._pending
        if record is None:
            raise AssertionError("pop on an exhausted record stream")
        self._pending = self._pull()
        return record


class MissionInventory:
    """Paged, streaming air-ground inventory and per-trace consistency check.

    ``air``/``ground`` are opened ``RcScanReader`` instances; the caller owns
    their lifecycle.  Every analysis method replays the two logical views as
    a deterministic merge-join in ascending ``trace_index`` order, holding at
    most one chunk plus the current page — raw arrays are never retained.
    """

    def __init__(
        self,
        air: RcScanReader,
        ground: RcScanReader,
        *,
        page_size: int = 1000,
    ) -> None:
        if not isinstance(air, RcScanReader) or not isinstance(ground, RcScanReader):
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT,
                "air and ground must be RcScanReader instances",
                {},
            )
        self._air = air
        self._ground = ground
        self._page_size = _require_page_size(page_size)
        self._index_sets: _SideSets | None = None

    # -- internals ----------------------------------------------------------

    def _ensure_index_sets(self) -> _SideSets:
        if self._index_sets is None:
            air_distinct, air_excluded = _side_index_sets(self._air)
            ground_distinct, ground_excluded = _side_index_sets(self._ground)
            self._index_sets = _SideSets(
                air_distinct,
                air_excluded,
                ground_distinct,
                ground_excluded,
            )
        return self._index_sets

    @staticmethod
    def _pair_item(air: ReadTrace, ground: ReadTrace) -> InventoryItem:
        index = air.trace_index
        if air.trace_uid != ground.trace_uid:
            return InventoryItem(
                InventoryItemKind.CONFLICT,
                index,
                air.trace_uid,
                ground.trace_uid,
                air.raw_trace_sha256,
                ground.raw_trace_sha256,
                "trace_uid mismatch",
            )
        if air.raw_trace_sha256 != ground.raw_trace_sha256:
            return InventoryItem(
                InventoryItemKind.CONFLICT,
                index,
                air.trace_uid,
                ground.trace_uid,
                air.raw_trace_sha256,
                ground.raw_trace_sha256,
                "raw_trace_sha256 mismatch",
            )
        detail = _gnss_diff_detail(air.metadata.gnss_match, ground.metadata.gnss_match)
        if detail is not None:
            return InventoryItem(
                InventoryItemKind.GNSS_DIFF,
                index,
                air.trace_uid,
                ground.trace_uid,
                air.raw_trace_sha256,
                ground.raw_trace_sha256,
                detail,
            )
        return InventoryItem(
            InventoryItemKind.CONSISTENT,
            index,
            air.trace_uid,
            ground.trace_uid,
            air.raw_trace_sha256,
            ground.raw_trace_sha256,
            None,
        )

    @staticmethod
    def _excluded_item(index: int, detail: str) -> InventoryItem:
        return InventoryItem(
            InventoryItemKind.CONFLICT,
            index,
            None,
            None,
            None,
            None,
            detail,
        )

    def _iter_items(self) -> Iterator[InventoryItem]:
        """Deterministic per-index classification stream (ascending index).

        An index excluded from one side's logical view (intra-file conflict)
        is emitted as a conflict at the position where the other side's
        stream reaches it — never silently reclassified as missing/extra.
        """
        sets = self._ensure_index_sets()
        excluded = sets.air_excluded | sets.ground_excluded
        air = _RecordStream(self._air)
        ground = _RecordStream(self._ground)
        while True:
            a = air.peek()
            g = ground.peek()
            if a is None:
                if g is None:
                    return
                index = g.trace_index
                ground.pop()
                if index in excluded:
                    yield self._excluded_item(index, "air intra-file conflict")
                else:
                    yield InventoryItem(
                        InventoryItemKind.EXTRA,
                        index,
                        None,
                        g.trace_uid,
                        None,
                        g.raw_trace_sha256,
                        None,
                    )
                continue
            if g is None:
                index = a.trace_index
                air.pop()
                if index in excluded:
                    yield self._excluded_item(index, "ground intra-file conflict")
                else:
                    yield InventoryItem(
                        InventoryItemKind.MISSING,
                        index,
                        a.trace_uid,
                        None,
                        a.raw_trace_sha256,
                        None,
                        None,
                    )
                continue
            air_index = a.trace_index
            ground_index = g.trace_index
            if air_index < ground_index:
                air.pop()
                if air_index in excluded:
                    yield self._excluded_item(air_index, "ground intra-file conflict")
                else:
                    yield InventoryItem(
                        InventoryItemKind.MISSING,
                        air_index,
                        a.trace_uid,
                        None,
                        a.raw_trace_sha256,
                        None,
                        None,
                    )
                continue
            if ground_index < air_index:
                ground.pop()
                if ground_index in excluded:
                    yield self._excluded_item(ground_index, "air intra-file conflict")
                else:
                    yield InventoryItem(
                        InventoryItemKind.EXTRA,
                        ground_index,
                        None,
                        g.trace_uid,
                        None,
                        g.raw_trace_sha256,
                        None,
                    )
                continue
            air.pop()
            ground.pop()
            yield self._pair_item(a, g)

    # -- public API ---------------------------------------------------------

    def contract(self) -> ContractReport:
        """Mission-level contract comparison (report-only)."""
        air = self._air
        ground = self._ground
        mission_id_match = air.mission_id == ground.mission_id
        channels_match = air.channels == ground.channels
        frequencies_match = bool(
            np.array_equal(air.frequencies_hz, ground.frequencies_hz)
        )
        config_match = (
            air.config.to_canonical_json() == ground.config.to_canonical_json()
        )
        issues: list[ContractIssue] = []
        if not mission_id_match:
            issues.append(
                ContractIssue(
                    "mission_id",
                    False,
                    air.mission_id.to_json(),
                    ground.mission_id.to_json(),
                    "mission ids differ",
                )
            )
        if not channels_match:
            issues.append(
                ContractIssue(
                    "channels",
                    False,
                    ",".join(channel.channel_id for channel in air.channels),
                    ",".join(channel.channel_id for channel in ground.channels),
                    "channel definitions differ",
                )
            )
        if not frequencies_match:
            issues.append(
                ContractIssue(
                    "frequencies",
                    False,
                    _axis_summary(air.frequencies_hz),
                    _axis_summary(ground.frequencies_hz),
                    "frequency axes differ",
                )
            )
        if not config_match:
            issues.append(
                ContractIssue(
                    "config",
                    False,
                    air.config.config_sha256,
                    ground.config.config_sha256,
                    "mission config digests differ",
                )
            )
        return ContractReport(
            mission_id_match,
            channels_match,
            frequencies_match,
            config_match,
            tuple(issues),
        )

    def summary(self) -> InventorySummary:
        """One streaming pass over the classification stream (O(1) memory)."""
        sets = self._ensure_index_sets()
        matched = 0
        missing = 0
        extra = 0
        conflicts = 0
        gnss_diffs = 0
        for item in self._iter_items():
            if item.kind is InventoryItemKind.MISSING:
                missing += 1
            elif item.kind is InventoryItemKind.EXTRA:
                extra += 1
            elif item.kind is InventoryItemKind.CONFLICT:
                conflicts += 1
            elif item.kind is InventoryItemKind.GNSS_DIFF:
                matched += 1
                gnss_diffs += 1
            else:
                matched += 1
        air_report = self._air.validation_report()
        ground_report = self._ground.validation_report()
        return InventorySummary(
            air_traces=len(sets.air_distinct),
            ground_traces=len(sets.ground_distinct),
            matched=matched,
            missing=missing,
            extra=extra,
            conflicts=conflicts,
            gnss_diffs=gnss_diffs,
            air_duplicates=len(air_report.duplicates),
            ground_duplicates=len(ground_report.duplicates),
            air_conflicts=len(air_report.conflicts),
            ground_conflicts=len(ground_report.conflicts),
            air_issues=len(air_report.issues),
            ground_issues=len(ground_report.issues),
        )

    def iter_items(
        self,
        *,
        kind: InventoryItemKind | None = None,
    ) -> Iterator[InventoryItem]:
        """Stream all classifications (optionally one kind), ascending index."""
        _require_kind(kind)
        for item in self._iter_items():
            if kind is None or item.kind is kind:
                yield item

    def page(
        self,
        page_index: int,
        *,
        kind: InventoryItemKind | None = None,
    ) -> InventoryPage:
        """Return one bounded, deterministic page of the item stream.

        The page window applies to items *after* the ``kind`` filter, so a
        kind-filtered page enumerates exactly that anomaly class (e.g. all
        ``missing`` entries for a ``missing_request`` replay).
        """
        _require_page_index(page_index)
        _require_kind(kind)
        start = page_index * self._page_size
        stop = start + self._page_size
        items: list[InventoryItem] = []
        has_more = False
        position = 0
        for item in self._iter_items():
            if kind is not None and item.kind is not kind:
                continue
            if position < start:
                position += 1
                continue
            if position < stop:
                items.append(item)
                position += 1
                continue
            has_more = True
            break
        return InventoryPage(page_index, self._page_size, has_more, tuple(items))

    def to_dict(self) -> dict[str, object]:
        """Stable, deterministic report serialization.

        The report carries the contract, the summary, both sides' reader
        validation reports and the anomaly items (missing/extra/conflict/
        gnss_diff); fully consistent traces are counted, not enumerated, so
        the common all-matching case stays compact.  Trace-level detail for
        any class is available through ``iter_items``/``page``.
        """
        anomalies = [
            item.to_dict()
            for item in self._iter_items()
            if item.kind is not InventoryItemKind.CONSISTENT
        ]
        return {
            "report_format": REPORT_FORMAT,
            "report_version": REPORT_VERSION,
            "air_path": str(self._air.path),
            "ground_path": str(self._ground.path),
            "page_size": self._page_size,
            "contract": self.contract().to_dict(),
            "summary": self.summary().to_dict(),
            "air_validation": self._air.validation_report().to_dict(),
            "ground_validation": self._ground.validation_report().to_dict(),
            "items": anomalies,
        }
