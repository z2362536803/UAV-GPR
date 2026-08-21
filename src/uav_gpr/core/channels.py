"""Channel contract: stable channel ID, logical polarization, S-parameter."""

from __future__ import annotations

import re
from dataclasses import dataclass

from uav_gpr.core.enums import LogicalPolarization, SParameter

_CHANNEL_ID_RE = re.compile(r"^[A-Za-z0-9_]+$")


@dataclass(frozen=True, slots=True)
class ChannelSpec:
    """One acquisition channel binding (immutable value object).

    ``channel_id`` is the stable in-file channel identifier (e.g. ``hh_s11``).
    Array channel order is always defined by the explicit ``channels`` tuple;
    it must never be inferred from dict iteration or UI order.
    """

    channel_id: str
    logical_polarization: LogicalPolarization
    s_parameter: SParameter
    display_name: str
    antenna_note: str | None = None

    def __post_init__(self) -> None:
        if (
            not isinstance(self.channel_id, str)
            or _CHANNEL_ID_RE.fullmatch(self.channel_id) is None
        ):
            raise ValueError(
                f"channel_id must match {_CHANNEL_ID_RE.pattern!r}, "
                f"got {self.channel_id!r}"
            )
        if not isinstance(self.logical_polarization, LogicalPolarization):
            raise TypeError(
                "logical_polarization must be a LogicalPolarization, "
                f"got {type(self.logical_polarization).__name__}"
            )
        if not isinstance(self.s_parameter, SParameter):
            raise TypeError(
                f"s_parameter must be an SParameter, got {type(self.s_parameter).__name__}"
            )
        if not isinstance(self.display_name, str) or not self.display_name:
            raise ValueError("display_name must be a non-empty string")
        if self.antenna_note is not None and not isinstance(self.antenna_note, str):
            raise TypeError("antenna_note must be a string or None")
