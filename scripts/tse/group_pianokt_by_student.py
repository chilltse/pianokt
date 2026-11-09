#!/usr/bin/env python3
"""Group training exports as students -> visits -> chronological attempts.

Each attempt stores column-oriented events_data, like alignment results.
Usage: python scripts/tse/group_pianokt_by_student.py input.json output.json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def parse_timestamp(value: Any) -> datetime | None:
    """Parse ISO 8601 into UTC; missing timestamps sort last."""
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ValueError(f"ordering_timestamp must be an ISO-8601 string, got {value!r}")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"Invalid ordering_timestamp: {value!r}") from exc
    if result.tzinfo is None:
        raise ValueError(f"Timestamp requires timezone: {value!r}")
    return result.astimezone(timezone.utc)


def chronological(value, identity):
    timestamp = parse_timestamp(value)
    return (timestamp is None, timestamp or datetime.max.replace(tzinfo=timezone.utc), identity)


def consistent(values, field):
    known = [value for value in values if value is not None]
    if known and any(value != known[0] for value in known):
        raise ValueError(f"Conflicting {field} within attempt")
    return known[0] if known else None


def group_export(data: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(data, dict) or not isinstance(data.get("examples"), list):
        raise ValueError('Expected a JSON object containing an "examples" array.')
    runs = {}
    for run in data.get("runs", []):
        aid = run["attempt_id"]
        if aid in runs:
            raise ValueError(f"Duplicate run for attempt {aid}")
        runs[aid] = run
    grouped = defaultdict(lambda: defaultdict(list))
    owners = {}
    for event in data["examples"]:
        learner, aid = event.get("learner_key"), event.get("attempt_id")
        if not isinstance(learner, str) or not learner or not isinstance(aid, str) or not aid:
            raise ValueError("Every event requires learner_key and attempt_id")
        if aid in owners and owners[aid] != learner:
            raise ValueError(f"Attempt {aid} belongs to multiple learners")
        owners[aid] = learner
        grouped[learner][aid].append(event)

    students = []
    visit_owners = {}
    shared = ("alignment_run_id", "song_id", "segment_id", "segment_index", "midi_sha256",
              "midi_keyboard_used", "visit_id", "ordering_source")
    columns = ("onset_seconds", "pitches", "intervals_seconds", "timing_offset_ms",
               "pitch_offset_semitones", "extra_notes")
    for learner, attempts in sorted(grouped.items()):
        visits = {}
        for aid, events in attempts.items():
            run = runs.get(aid, {})
            indices = [e.get("event_index") for e in events]
            if any(type(i) is not int for i in indices) or sorted(indices) != list(range(len(events))):
                raise ValueError(f"Noncontiguous or duplicate event_index: {aid}")
            events = sorted(events, key=lambda e: e["event_index"])
            attempt = {**run, "attempt_id": aid}
            for key in shared:
                attempt[key] = consistent([run.get(key), *(e.get(key) for e in events)], key)
            timestamps = [parse_timestamp(e.get("ordering_timestamp")) for e in events]
            timestamp = consistent(timestamps, "ordering_timestamp")
            attempt["ordering_timestamp"] = timestamp.isoformat() if timestamp else None
            attempt["kc_labeler_version"] = run.get("kc_labeler_version", data.get("kc_labeler_version"))
            count = run.get("reference_events_count")
            if count is not None and count != len(events):
                raise ValueError(f"Reference event count mismatch: {aid}")
            attempt["reference_events_count"] = len(events)
            event_data = {key: [e[key] for e in events] for key in columns}
            if any(a >= b for a, b in zip(event_data["onset_seconds"], event_data["onset_seconds"][1:])):
                raise ValueError(f"Onsets are not strictly increasing: {aid}")
            event_data["kc_vectors"] = [e["kc_vector"] for e in events]
            event_data["reject_reason"] = {
                key: [e["reject_reason"][key] for e in events] for key in ("event", "note")}
            event_data["correct"] = [e["correct"] for e in events]
            if any(e["correct"] != 1 - e["reject_reason"]["event"] for e in events):
                raise ValueError(f"Correct/reject_reason conflict: {aid}")
            names = attempt.pop("kc_feature_names", None)
            if names is not None:
                event_data = {"kc_feature_names": names, **event_data}
            # Preserve future event-level extensions without duplicating shared identity.
            consumed = set(columns) | set(shared) | {
                "learner_key", "attempt_id", "event_index", "ordering_timestamp",
                "kc_vector", "reject_reason", "correct"}
            for key in sorted(set().union(*(e.keys() for e in events)) - consumed):
                event_data[key] = [e.get(key) for e in events]
            attempt["events_data"] = event_data
            vid = attempt["visit_id"]
            if not isinstance(vid, str) or not vid:
                raise ValueError(f"Missing visit_id: {aid}; re-export training with current CLI")
            if vid in visit_owners and visit_owners[vid] != learner:
                raise ValueError(f"Visit {vid} belongs to multiple learners")
            visit_owners[vid] = learner
            visits.setdefault(vid, []).append(attempt)

        ordered_visits = []
        for vid, items in visits.items():
            items.sort(key=lambda a: chronological(a["ordering_timestamp"], a["attempt_id"]))
            starts = [parse_timestamp(a.pop("visit_started_at", None)) for a in items]
            start = consistent(starts, "visit_started_at")
            fallback = min((parse_timestamp(a["ordering_timestamp"]) for a in items
                            if a["ordering_timestamp"] is not None), default=None)
            order = start or fallback
            ordered_visits.append({
                "visit_id": vid,
                "started_at": start.isoformat() if start else None,
                "ordering_timestamp": order.isoformat() if order else None,
                "ordering_source": "visit_started_at" if start else "earliest_exported_attempt",
                "attempts": items,
            })
        ordered_visits.sort(key=lambda v: chronological(v["ordering_timestamp"], v["visit_id"]))
        students.append({"learner_key": learner, "visits": ordered_visits})
    return {
        **{key: value for key, value in data.items() if key not in ("examples", "runs")},
        "grouping_schema_version": 2,
        "students": students,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="PianoKT export-training JSON (may end in .txt)")
    parser.add_argument("output", nargs="?", type=Path, help="Output JSON path (optional)")
    args = parser.parse_args()
    output = args.output or args.input.with_name(args.input.stem + ".by_student.json")
    if args.input.resolve() == output.resolve():
        parser.error("Input and output paths must be different, to protect the original file")

    with args.input.open("r", encoding="utf-8-sig") as f:
        grouped = group_export(json.load(f))

    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as f:
        json.dump(grouped, f, ensure_ascii=False, indent=2, allow_nan=False)
        f.write("\n")

    students = grouped["students"]
    visits = [v for student in students for v in student["visits"]]
    attempts = [a for visit in visits for a in visit["attempts"]]
    n_events = sum(a["reference_events_count"] for a in attempts)
    print(f"Saved: {output}")
    print(f"Students: {len(students)} | Visits: {len(visits)} | Attempts: {len(attempts)} | Events: {n_events}")



if __name__ == "__main__":
    main()
