"""ISSUE-012: read-only partial inspection and non-destructive recovery.

This module implements the crash-recovery half of the reliable local storage
contract on top of the ISSUE-010 writer semantics
(:mod:`uav_gpr.storage.incremental_writer`), the ISSUE-011 strict reader
(:mod:`uav_gpr.storage.rcscan_reader`) and the ISSUE-008 frozen schema
(:mod:`uav_gpr.storage.rcscan_v2`).  ``docs/DATA_FORMAT.md`` section 4:

1. never overwrite, truncate or delete the source partial;
2. scan schema / checkpoint / dataset lengths / per-trace hashes read-only;
3. produce a structured report;
4. after explicit confirmation, copy the last complete commit point into a
   **new** recovered ``.rcscan`` file;
5. the source partial stays byte-identical on disk.

API (``docs/plans/2026-08-30-issue-012-recovery.md`` section 1 decisions):

- :func:`inspect_partial` — deterministic, serializable read-only report
  (schema, checkpoint, per-column lengths, half-written tail, ISSUE-011
  classification and source SHA-256).  Never mutates the file.
- :func:`plan_recovery` — **dry-run by default**: decides the new file id and
  target path, gates recoverability (only ``writing`` ``*.partial.rcscan``
  sources, no optional processed groups, no existing target) and surfaces
  data-level warnings.  Never writes anything.
- :func:`execute_recovery` — the explicit, confirmed action: re-validates the
  source SHA-256 and the target, creates a fresh skeleton with a new
  ``file_id``, copies the committed rows ``[0, committed_record_count)`` as
  **raw physical cells** (never re-decoding or re-hashing — evidence such as
  duplicates, conflicts and missing-hash rows is preserved verbatim and
  re-reported by the strict reader), writes ``completion_kind=recovered`` and
  the provenance attributes, verifies the result with the strict reader and
  only then atomically renames the temporary partial into the final
  ``<new_file_id>.rcscan``.  Any failure removes the temporary file, so a
  failed recovery never leaves a pseudo-finalized artifact.

Out of scope: in-place truncation/repair, automatic deletion of source files,
GUI, v1 migration (ISSUE-013), inventory (ISSUE-014), processing.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol, cast

import h5py  # type: ignore[import-untyped]
import numpy as np

from uav_gpr.core.enums import EndpointRole
from uav_gpr.core.errors import DomainError, ErrorCode, JsonValue
from uav_gpr.core.identifiers import AirFileId, GroundFileId
from uav_gpr.core.timeutil import Clock, SystemClock, ensure_utc, from_utc_iso, to_utc_iso
from uav_gpr.storage import rcscan_v2 as schema
from uav_gpr.storage.incremental_writer import FINAL_SUFFIX, PARTIAL_SUFFIX
from uav_gpr.storage.rcscan_reader import RcScanReader, RcScanValidator, ValidationReport

__all__ = [
    "RECOVERY_COMPONENT_VERSION",
    "InjectedRecoveryFault",
    "InspectReport",
    "LocalRecoveryFileSystem",
    "RecoveryFaultHook",
    "RecoveryFileSystem",
    "RecoveryPhase",
    "RecoveryPlan",
    "RecoveryResult",
    "execute_recovery",
    "inspect_partial",
    "plan_recovery",
]

#: Recovery tool component version, recorded as the recovered file's
#: ``writer_version`` root attribute and in the returned results.
RECOVERY_COMPONENT_VERSION = "issue012.1"

#: Trace-major column groups the ISSUE-010 writer commits per logical trace.
_ROW_GROUPS = ("/trace_metadata", "/gnss", "/acquisition", "/transport")

#: Optional processed groups that ISSUE-012 recovery does not copy.
_OPTIONAL_GROUP_DATASETS = (
    "/axes/time_base_s",
    "/axes/time_processed_s",
    "/frequency/calibrated",
    "/time_base/data",
    "/time_processed/data",
)

#: Physical rows copied per column slice (bounded memory on large files).
_COPY_CHUNK = 512


class InjectedRecoveryFault(RuntimeError):
    """Deterministic fault raised by :class:`RecoveryFaultHook`."""


class RecoveryPhase(StrEnum):
    """Every observable step of one recovery execution (fault seams)."""

    BEFORE_TARGET_CREATE = "before_target_create"
    AFTER_TARGET_CREATE = "after_target_create"
    AFTER_ROW_COPY = "after_row_copy"
    AFTER_CHECKPOINT_WRITE = "after_checkpoint_write"
    BEFORE_FINAL_MARK = "before_final_mark"
    AFTER_FINAL_MARK = "after_final_mark"
    BEFORE_RENAME = "before_rename"


class RecoveryFaultHook:
    """Mutable fault-injection hook: arms named phases to raise on demand.

    Mirrors the ISSUE-010 ``PhaseFaultHook`` pattern so the recovery failure
    matrix is deterministic (no sleeps, no timing guessing).
    """

    def __init__(
        self,
        *phases: RecoveryPhase,
        exception: type[BaseException] = InjectedRecoveryFault,
    ) -> None:
        self._armed: set[RecoveryPhase] = set(phases)
        self._exception = exception
        self._observed: list[RecoveryPhase] = []

    def arm(self, *phases: RecoveryPhase) -> None:
        self._armed.update(phases)

    def disarm(self, *phases: RecoveryPhase) -> None:
        self._armed.difference_update(phases)

    @property
    def observed(self) -> tuple[RecoveryPhase, ...]:
        return tuple(self._observed)

    @property
    def armed(self) -> frozenset[RecoveryPhase]:
        return frozenset(self._armed)

    def on_phase(self, phase: RecoveryPhase) -> None:
        self._observed.append(phase)
        if phase in self._armed:
            raise self._exception(f"injected recovery fault at phase {phase.value}")


class RecoveryFileSystem(Protocol):
    """The only filesystem surface recovery uses (injectable for tests)."""

    def exists(self, path: Path) -> bool:
        """Whether ``path`` exists (collision guards before create/rename)."""
        ...

    def remove(self, path: Path) -> None:
        """Remove one file (best-effort cleanup after a failed recovery)."""
        ...

    def replace(self, source: Path, target: Path) -> None:
        """Atomically move ``source`` to ``target`` (final publication)."""
        ...


class LocalRecoveryFileSystem:
    """Production facade: ``Path.exists``, ``Path.unlink`` and ``os.replace``."""

    def exists(self, path: Path) -> bool:
        return path.exists()

    def remove(self, path: Path) -> None:
        path.unlink()

    def replace(self, source: Path, target: Path) -> None:
        os.replace(source, target)


# ---------------------------------------------------------------------------
# Shared read-only facts
# ---------------------------------------------------------------------------


def _file_sha256(path: Path) -> str:
    """Streaming SHA-256 of a file (bounded memory, never whole-file in RAM)."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _row_paths(h5: Any) -> tuple[str, ...]:
    """Required trace-major dataset paths present in an opened file.

    The reader has already validated every present dataset against the frozen
    contract, so enumerating the four row groups yields exactly the writer's
    row column set (``/transport`` absent for ground files).
    """
    paths: list[str] = []
    for group in _ROW_GROUPS:
        node = h5.get(group)
        if node is None:
            continue
        for name in node.keys():
            child = node[name]
            if isinstance(child, h5py.Dataset):
                paths.append(f"{group}/{name}")
    return tuple(paths)


def _text(value: object) -> str:
    """Decode a stored text cell (bytes for fixed ASCII, str for vlen)."""
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value)


def _mission_attr_text(attrs: Mapping[Any, Any], name: str) -> str:
    return _text(attrs[name])


def _source_mission_facts(source: Path) -> dict[str, object]:
    """Read the mission-level facts needed to rebuild a recovered skeleton.

    Read-only access; the source file is never opened for writing anywhere in
    this module.
    """
    with h5py.File(source, "r") as h5:
        mission = h5["mission"].attrs
        created_raw = _mission_attr_text(mission, "created_utc")
        started_raw = mission.get("started_utc")
        return {
            "created_utc": from_utc_iso(created_raw),
            "started_utc": (
                from_utc_iso(str(started_raw)) if started_raw else None
            ),
            "source_file_id": _mission_attr_text(h5.attrs, "file_id"),
            "last_trace_index": int(h5["/checkpoints/last_trace_index"][0]),
            "row_paths": _row_paths(h5),
            "optional_groups": tuple(
                path for path in _OPTIONAL_GROUP_DATASETS if path in h5
            ),
        }


# ---------------------------------------------------------------------------
# Read-only inspection
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class InspectReport:
    """Structured, deterministic, serializable outcome of a read-only scan.

    ``column_lengths`` covers every required trace-major column plus
    ``/frequency/raw``; ``physical_record_count`` is the shortest durable
    column (the reader's own window) and ``tail_rows`` is the longest column
    minus the checkpoint (the half-written tail).
    """

    path: str
    source_sha256: str
    format_name: str
    schema_version: int
    profile: str
    file_role: str
    file_id: str
    writer_version: str
    lifecycle_state: str
    mission_id: str
    device_id: str
    created_utc: datetime
    started_utc: datetime | None
    ended_utc: datetime | None
    completion_kind: str
    config_sha256: str
    committed_record_count: int
    physical_record_count: int
    tail_rows: int
    last_trace_index: int
    checkpoint_updated_utc: datetime
    column_lengths: dict[str, int]
    optional_groups_present: tuple[str, ...]
    validation: ValidationReport

    def to_dict(self) -> dict[str, object]:
        """Plain JSON-safe serialization (stable key order)."""
        return {
            "path": self.path,
            "source_sha256": self.source_sha256,
            "format_name": self.format_name,
            "schema_version": self.schema_version,
            "profile": self.profile,
            "file_role": self.file_role,
            "file_id": self.file_id,
            "writer_version": self.writer_version,
            "lifecycle_state": self.lifecycle_state,
            "mission_id": self.mission_id,
            "device_id": self.device_id,
            "created_utc": to_utc_iso(self.created_utc),
            "started_utc": (
                to_utc_iso(self.started_utc) if self.started_utc is not None else None
            ),
            "ended_utc": (
                to_utc_iso(self.ended_utc) if self.ended_utc is not None else None
            ),
            "completion_kind": self.completion_kind,
            "config_sha256": self.config_sha256,
            "committed_record_count": self.committed_record_count,
            "physical_record_count": self.physical_record_count,
            "tail_rows": self.tail_rows,
            "last_trace_index": self.last_trace_index,
            "checkpoint_updated_utc": to_utc_iso(self.checkpoint_updated_utc),
            "column_lengths": dict(self.column_lengths),
            "optional_groups_present": list(self.optional_groups_present),
            "validation": self.validation.to_dict(),
        }


def inspect_partial(path: str | Path) -> InspectReport:
    """Read-only structured inspection of an ``.rcscan`` v2 file.

    Schema-level problems (unknown version/profile, corrupt checkpoint,
    shortened columns, non-HDF5 payload) fail closed with ``DomainError``
    exactly like :class:`RcScanReader`; data-level problems are classified
    inside ``validation``.  The source file is never modified.
    """
    source = Path(path)
    source_sha256 = _file_sha256(source)
    reader = RcScanReader(source)
    try:
        facts = _source_mission_facts(source)
        with h5py.File(source, "r") as h5:
            mission = h5["mission"].attrs
            created_raw = _mission_attr_text(mission, "created_utc")
            started_raw = mission.get("started_utc")
            ended_raw = mission.get("ended_utc")
            row_paths = cast(tuple[str, ...], facts["row_paths"])
            column_lengths = {
                path: int(h5[path].shape[0]) for path in row_paths
            }
            column_lengths["/frequency/raw"] = int(h5["/frequency/raw"].shape[0])
            optional_groups = cast(
                tuple[str, ...], facts["optional_groups"]
            )
            checkpoint_updated = from_utc_iso(
                _text(h5["/checkpoints/updated_utc"][0])
            )
            last_trace_index = int(h5["/checkpoints/last_trace_index"][0])
            committed = int(h5["/checkpoints/committed_record_count"][0])
        return InspectReport(
            path=str(source),
            source_sha256=source_sha256,
            format_name=reader.probe.format_name,
            schema_version=reader.probe.schema_version,
            profile=reader.probe.profile,
            file_role=reader.probe.file_role.value,
            file_id=reader.probe.file_id,
            writer_version=reader.probe.writer_version,
            lifecycle_state=reader.probe.lifecycle_state,
            mission_id=str(reader.mission_id),
            device_id=str(reader.device_id),
            created_utc=from_utc_iso(created_raw),
            started_utc=from_utc_iso(str(started_raw)) if started_raw else None,
            ended_utc=from_utc_iso(str(ended_raw)) if ended_raw else None,
            completion_kind=reader.completion_kind,
            config_sha256=reader.config.config_sha256,
            committed_record_count=committed,
            physical_record_count=min(column_lengths.values()),
            tail_rows=max(column_lengths.values()) - committed,
            last_trace_index=last_trace_index,
            checkpoint_updated_utc=checkpoint_updated,
            column_lengths=column_lengths,
            optional_groups_present=optional_groups,
            validation=reader.validation_report(),
        )
    finally:
        reader.close()


# ---------------------------------------------------------------------------
# Dry-run planning
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RecoveryPlan:
    """Decided, dry-run recovery plan (never writes anything).

    ``recoverable`` is ``False`` with ``blocked_reasons`` for sources that
    must not be recovered (non-partial name, non-writing lifecycle, optional
    processed groups, target already exists).  Data-level issues that are
    preserved verbatim by the copy are surfaced as ``warnings``.
    """

    source_path: str
    target_path: str
    new_file_id: str
    file_role: str
    source_sha256: str
    committed_record_count: int
    physical_record_count: int
    tail_rows: int
    recoverable: bool
    blocked_reasons: tuple[str, ...]
    warnings: tuple[str, ...]
    planned_utc: datetime
    tool_version: str

    def to_dict(self) -> dict[str, object]:
        """Plain JSON-safe serialization (stable key order)."""
        return {
            "source_path": self.source_path,
            "target_path": self.target_path,
            "new_file_id": self.new_file_id,
            "file_role": self.file_role,
            "source_sha256": self.source_sha256,
            "committed_record_count": self.committed_record_count,
            "physical_record_count": self.physical_record_count,
            "tail_rows": self.tail_rows,
            "recoverable": self.recoverable,
            "blocked_reasons": list(self.blocked_reasons),
            "warnings": list(self.warnings),
            "planned_utc": to_utc_iso(self.planned_utc),
            "tool_version": self.tool_version,
        }


def plan_recovery(
    path: str | Path,
    *,
    new_file_id: AirFileId | GroundFileId | None = None,
    target_dir: str | Path | None = None,
    clock: Clock | None = None,
) -> RecoveryPlan:
    """Build the recovery plan for a crashed partial — **dry-run, no writes**.

    ``new_file_id`` defaults to a fresh role-typed id; ``target_dir`` defaults
    to the source directory.  The planned target is
    ``<target_dir>/<new_file_id>.rcscan`` and must not already exist.
    """
    source = Path(path)
    now = ensure_utc((clock if clock is not None else SystemClock()).utc_now())
    inspect = inspect_partial(source)
    role = EndpointRole.from_value(inspect.file_role)

    blocked: list[str] = []
    if not source.name.endswith(PARTIAL_SUFFIX):
        blocked.append("source is not a *.partial.rcscan file")
    if inspect.lifecycle_state != "writing":
        blocked.append(
            f"lifecycle_state is {inspect.lifecycle_state!r}; "
            "only writing partials are recoverable"
        )
    if inspect.optional_groups_present:
        blocked.append(
            "source carries optional processed groups that recovery does not copy"
        )

    if new_file_id is None:
        generated = (
            AirFileId.new() if role is EndpointRole.AIR else GroundFileId.new()
        )
    else:
        expected = AirFileId if role is EndpointRole.AIR else GroundFileId
        if not isinstance(new_file_id, expected):
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT,
                "new_file_id must be the role-typed file id for the source role",
                {
                    "new_file_id_type": type(new_file_id).__name__,
                    "expected": expected.__name__,
                    "file_role": inspect.file_role,
                },
            )
        generated = new_file_id
    target_dir_path = Path(target_dir) if target_dir is not None else source.parent
    target = target_dir_path / f"{generated}{FINAL_SUFFIX}"

    warnings: list[str] = []
    summary = inspect.validation.summary()
    if (
        summary["missing"]
        or summary["duplicates"]
        or summary["conflicts"]
        or summary["issues"]
    ):
        warnings.append(
            "data-level issues are preserved verbatim by the copy and "
            "re-reported by the strict reader: "
            f"{summary['missing']} missing, {summary['duplicates']} duplicates, "
            f"{summary['conflicts']} conflicts, {summary['issues']} row issues"
        )
    if target.exists():
        blocked.append(f"recovery target already exists: {target}")

    return RecoveryPlan(
        source_path=str(source),
        target_path=str(target),
        new_file_id=str(generated),
        file_role=inspect.file_role,
        source_sha256=inspect.source_sha256,
        committed_record_count=inspect.committed_record_count,
        physical_record_count=inspect.physical_record_count,
        tail_rows=inspect.tail_rows,
        recoverable=not blocked,
        blocked_reasons=tuple(blocked),
        warnings=tuple(warnings),
        planned_utc=now,
        tool_version=RECOVERY_COMPONENT_VERSION,
    )


# ---------------------------------------------------------------------------
# Explicit, confirmed execution
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RecoveryResult:
    """Outcome of a successful recovery execution."""

    source_path: str
    target_path: str
    new_file_id: str
    source_sha256: str
    recovered_utc: datetime
    copied_record_count: int
    tool_version: str
    post_validation: ValidationReport

    def to_dict(self) -> dict[str, object]:
        """Plain JSON-safe serialization (stable key order)."""
        return {
            "source_path": self.source_path,
            "target_path": self.target_path,
            "new_file_id": self.new_file_id,
            "source_sha256": self.source_sha256,
            "recovered_utc": to_utc_iso(self.recovered_utc),
            "copied_record_count": self.copied_record_count,
            "tool_version": self.tool_version,
            "post_validation": self.post_validation.to_dict(),
        }


def _copy_committed_rows(
    source: Path, temp: Path, row_paths: tuple[str, ...], committed: int
) -> None:
    """Copy physical rows ``[0, committed)`` column-by-column, chunked.

    Raw cell copy only: nothing is decoded or re-hashed, so duplicates,
    conflicts, missing-hash rows and undecodable rows survive verbatim and
    are re-reported by the strict reader.
    """
    paths = (*row_paths, "/frequency/raw")
    with h5py.File(source, "r") as src, h5py.File(temp, "r+") as out:
        for path in paths:
            src_dataset = src[path]
            out_dataset = out[path]
            shape = list(out_dataset.shape)
            shape[0] = committed
            out_dataset.resize(tuple(shape))
            for start in range(0, committed, _COPY_CHUNK):
                stop = min(start + _COPY_CHUNK, committed)
                out_dataset[start:stop] = src_dataset[start:stop]


def execute_recovery(
    plan: RecoveryPlan,
    *,
    clock: Clock | None = None,
    fault_hook: RecoveryFaultHook | None = None,
    filesystem: RecoveryFileSystem | None = None,
) -> RecoveryResult:
    """Explicitly recover the planned partial into a new recovered file.

    Calling this function is the explicit confirmation required by the
    dry-run default (the acceptance criterion "未经确认只 dry-run"): it is
    the only code path that writes the recovered artifact.

    Guarantees:

    - the source partial is opened read-only and its bytes never change;
    - the target (and its temporary staging name) must not exist;
    - the source SHA-256 must still match the plan (a changed source is
      refused fail-closed);
    - the committed rows are copied verbatim into a fresh skeleton with a new
      ``file_id``, ``completion_kind=recovered`` and provenance attributes;
    - the staged file is verified with the strict reader **before** the
      atomic rename publishes it;
    - any failure removes the staged file (best-effort); if even the removal
      fails, the explicit error carries the leftover path and the remnant is
      partial-named — never a pseudo-finalized ``.rcscan``.
    """
    if not isinstance(plan, RecoveryPlan):
        raise TypeError(
            f"plan must be a RecoveryPlan, got {type(plan).__name__}"
        )
    if not plan.recoverable:
        raise DomainError(
            ErrorCode.INVALID_ARGUMENT,
            "recovery plan is not executable",
            {"blocked_reasons": cast(JsonValue, list(plan.blocked_reasons))},
        )
    source = Path(plan.source_path)
    target = Path(plan.target_path)
    fs = filesystem if filesystem is not None else LocalRecoveryFileSystem()
    hook = fault_hook if fault_hook is not None else RecoveryFaultHook()
    now = ensure_utc((clock if clock is not None else SystemClock()).utc_now())

    # 1. fail-closed re-validation of the world since the plan was built.
    if not fs.exists(source):
        raise DomainError(
            ErrorCode.INVALID_ARGUMENT,
            "source partial is missing",
            {"path": plan.source_path},
        )
    if _file_sha256(source) != plan.source_sha256:
        raise DomainError(
            ErrorCode.INVALID_ARGUMENT,
            "source partial changed since the plan was built",
            {"path": plan.source_path},
        )
    if fs.exists(target):
        raise DomainError(
            ErrorCode.INVALID_ARGUMENT,
            "recovery target already exists",
            {"path": plan.target_path},
        )
    temp = target.with_name(f"{target.stem}{PARTIAL_SUFFIX}")
    if fs.exists(temp):
        raise DomainError(
            ErrorCode.INVALID_ARGUMENT,
            "recovery staging path already exists",
            {"path": str(temp)},
        )

    # 2. Re-read the source facts (read-only) and stage a fresh skeleton.
    try:
        hook.on_phase(RecoveryPhase.BEFORE_TARGET_CREATE)
        reader = RcScanReader(source)
        try:
            facts = _source_mission_facts(source)
            row_paths = cast(tuple[str, ...], facts["row_paths"])
            created_utc = cast(datetime, facts["created_utc"])
            started_utc = cast("datetime | None", facts["started_utc"])
            last_trace_index = cast(int, facts["last_trace_index"])
            source_file_id = cast(str, facts["source_file_id"])
            optional_groups = cast(tuple[str, ...], facts["optional_groups"])
            mission_id = reader.mission_id
            device_id = reader.device_id
            channels = reader.channels
            frequencies_hz = reader.frequencies_hz
            committed = reader.committed_record_count
            role = reader.probe.file_role
            config = reader.config
        finally:
            reader.close()

        if optional_groups:
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT,
                "source carries optional processed groups that recovery does not copy",
                {"optional_groups": cast(JsonValue, list(optional_groups))},
            )

        expected_id = AirFileId if role is EndpointRole.AIR else GroundFileId
        new_file_id = expected_id.from_json(plan.new_file_id)
        schema.create_rcscan_v2(
            temp,
            mission_id=mission_id,
            device_id=device_id,
            file_id=new_file_id,
            created_utc=created_utc,
            completed_utc=None,
            completion_kind=None,
            file_role=role,
            channels=channels,
            frequencies_hz=frequencies_hz,
            config_json=config.to_canonical_json(),
            config_sha256=config.config_sha256,
            writer_version=RECOVERY_COMPONENT_VERSION,
        )
        hook.on_phase(RecoveryPhase.AFTER_TARGET_CREATE)

        # 3. Copy the committed rows verbatim (bounded chunks).
        _copy_committed_rows(source, temp, row_paths, committed)
        hook.on_phase(RecoveryPhase.AFTER_ROW_COPY)

        # 4. Publish the checkpoint of the recovered file.
        with h5py.File(temp, "r+") as out:
            out["/checkpoints/committed_record_count"][0] = np.int64(committed)
            out["/checkpoints/last_trace_index"][0] = np.int64(last_trace_index)
            out["/checkpoints/updated_utc"][0] = to_utc_iso(now)
        hook.on_phase(RecoveryPhase.AFTER_CHECKPOINT_WRITE)

        # 5. Mission end state + provenance, then the recovered lifecycle.
        hook.on_phase(RecoveryPhase.BEFORE_FINAL_MARK)
        with h5py.File(temp, "r+") as out:
            mission = out["mission"]
            if started_utc is not None:
                mission.attrs["started_utc"] = to_utc_iso(started_utc)
            mission.attrs["ended_utc"] = to_utc_iso(now)
            mission.attrs["completion_kind"] = "recovered"
            mission.attrs["recovery_source_sha256"] = plan.source_sha256
            mission.attrs["recovery_source_file_id"] = source_file_id
            mission.attrs["recovery_tool_version"] = RECOVERY_COMPONENT_VERSION
            out.attrs["lifecycle_state"] = "recovered"
            out.flush()
        hook.on_phase(RecoveryPhase.AFTER_FINAL_MARK)

        # 6. Verify with the strict reader *before* publishing anything.
        with RcScanReader(temp) as check:
            if check.lifecycle_state != "recovered":
                raise DomainError(
                    ErrorCode.INVALID_ARGUMENT,
                    "recovered file failed lifecycle verification",
                    {"lifecycle_state": check.lifecycle_state},
                )
            if check.committed_record_count != committed:
                raise DomainError(
                    ErrorCode.INVALID_ARGUMENT,
                    "recovered file failed checkpoint verification",
                    {
                        "recovered_committed": check.committed_record_count,
                        "expected_committed": committed,
                    },
                )

        # 7. Atomic publication; never overwrite an existing target.
        if fs.exists(target):
            raise DomainError(
                ErrorCode.INVALID_ARGUMENT,
                "recovery target already exists",
                {"path": plan.target_path},
            )
        hook.on_phase(RecoveryPhase.BEFORE_RENAME)
        fs.replace(temp, target)
    except BaseException as error:
        _cleanup_failed_recovery(temp, fs, error)
        raise

    post = RcScanValidator.validate(target)
    return RecoveryResult(
        source_path=plan.source_path,
        target_path=plan.target_path,
        new_file_id=plan.new_file_id,
        source_sha256=plan.source_sha256,
        recovered_utc=now,
        copied_record_count=committed,
        tool_version=RECOVERY_COMPONENT_VERSION,
        post_validation=post,
    )


def _cleanup_failed_recovery(
    temp: Path, fs: RecoveryFileSystem, error: BaseException
) -> None:
    """Best-effort removal of the staged file after a failed recovery.

    If even the removal fails, the explicit error names the leftover path;
    the remnant is partial-named (never a final ``.rcscan``), so it can never
    be mistaken for a pseudo-finalized artifact.
    """
    if not fs.exists(temp):
        return
    try:
        fs.remove(temp)
    except BaseException:
        raise DomainError(
            ErrorCode.INVALID_ARGUMENT,
            "recovery failed and the partial result could not be removed; "
            "leftover path: " + str(temp),
            {"leftover_path": str(temp), "original_error": str(error)},
        ) from error
