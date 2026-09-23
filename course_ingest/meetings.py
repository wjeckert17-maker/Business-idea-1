"""Shared meeting-pattern normalization. Adapters extract fields; this decides whether the
combination is one the schema can represent, and if not, why."""
from __future__ import annotations

from datetime import date, time
from typing import Optional, Sequence

from .models import MeetingRecord


class UnrecognizedMeeting(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def normalize_meeting(
    *,
    kind: str,
    section_modality: str,
    block_modality: Optional[str],
    days: Sequence[int],
    start_time: Optional[time],
    end_time: Optional[time],
    start_date: Optional[date],
    end_date: Optional[date],
    campus: Optional[str] = None,
    building_code: Optional[str] = None,
    building_name: Optional[str] = None,
    room: Optional[str] = None,
    location_text: Optional[str] = None,
) -> MeetingRecord:
    """Turn extracted fields into a MeetingRecord or raise UnrecognizedMeeting.

    Rules (mirroring the CHECK constraints on meeting_block):
      * dates are required
      * async block: no days, no times
      * timed block: end > start, and either days or a single-date span
      * days without times = "arranged": days kept for display, time_tba set, no conflict slots
      * times without days on a multi-day span is not a pattern we know
    """
    if start_date is None or end_date is None:
        raise UnrecognizedMeeting("missing start/end date")
    if end_date < start_date:
        raise UnrecognizedMeeting("end date before start date")

    days_t = tuple(sorted(set(int(d) for d in days)))
    if any(d < 1 or d > 7 for d in days_t):
        raise UnrecognizedMeeting(f"weekday out of range: {days_t}")
    single_day = start_date == end_date
    has_times = start_time is not None or end_time is not None

    if has_times and (start_time is None or end_time is None):
        raise UnrecognizedMeeting("only one of begin/end time present")
    if has_times and end_time <= start_time:
        raise UnrecognizedMeeting("end time not after start time")
    if has_times and not days_t and not single_day:
        raise UnrecognizedMeeting("times given but no weekdays on a multi-day span")

    if not has_times:
        # No clock time: an online section's block is asynchronous; anything else is TBA.
        online_section = section_modality in ("online_async", "online_sync", "hybrid")
        online_block = block_modality in ("online_async", "online_sync")
        if online_section or online_block:
            modality, time_tba = "online_async", False
        else:
            modality, time_tba = "in_person", True
        return MeetingRecord(
            kind=kind, modality=modality,
            days=days_t if (days_t and time_tba) else None,   # arranged-on-MWF keeps its days
            start_time=None, end_time=None,
            start_date=start_date, end_date=end_date, time_tba=time_tba,
            campus=campus, building_code=building_code, building_name=building_name, room=room,
            location_tba=not (building_code or room), location_text=location_text,
        )

    modality = block_modality or ("online_sync" if section_modality == "online_sync" else "in_person")
    if modality == "online_async":
        # A timed meeting cannot be asynchronous; the code map is wrong for this block.
        modality = "online_sync"
    if modality == "hybrid":
        modality = "in_person"
    return MeetingRecord(
        kind=kind, modality=modality,
        days=days_t or None,                # None + single day => trigger derives the weekday
        start_time=start_time, end_time=end_time,
        start_date=start_date, end_date=end_date, time_tba=False,
        campus=campus, building_code=building_code, building_name=building_name, room=room,
        location_tba=not (building_code or room), location_text=location_text,
    )
