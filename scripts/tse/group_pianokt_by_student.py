#!/usr/bin/env python3
"""Group a PianoKT export-training JSON by learner, then chronological attempt.

Input:  {"examples": [{"learner_key": ..., "attempt_id": ..., ...}], ...}
Output: {"students": [{"learner_key": ..., "attempts": [{"attempt_id": ...,
         "ordering_timestamp": ..., "events": [...]}, ...]}], ...}

All original example fields are kept under each attempt's ``events`` array.
Other top-level export metadata, including ``runs`` and version fields, is kept.

Usage:
    python group_pianokt_by_student.py training.json
    python group_pianokt_by_student.py training.json grouped.json
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
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def event_sort_key(event: dict[str, Any]) -> tuple[float, float]:
    def numeric(name: str) -> float:
        value = event.get(name)
        return float(value) if value is not None else float("inf")

    return (numeric("event_index"), numeric("onset_seconds"))


def group_export(data: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(data, dict) or not isinstance(data.get("examples"), list):
        raise ValueError('Expected a JSON object containing an "examples" array.')

    grouped: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for i, event in enumerate(data["examples"]):
        if not isinstance(event, dict):
            raise ValueError(f"examples[{i}] is not a JSON object")
        learner = event.get("learner_key")
        attempt = event.get("attempt_id")
        if not isinstance(learner, str) or not learner.strip():
            raise ValueError(f"examples[{i}] is missing learner_key")
        if not isinstance(attempt, str) or not attempt.strip():
            raise ValueError(f"examples[{i}] is missing attempt_id")
        grouped[learner][attempt].append(event)

    students = []
    for learner_key, attempts_by_id in sorted(grouped.items()):
        attempts = []
        for attempt_id, events in attempts_by_id.items():
            # Use the earliest known timestamp to chronologically place the attempt.
            known_timestamps = [
                (parse_timestamp(e.get("ordering_timestamp")), e.get("ordering_timestamp"))
                for e in events
            ]
            known_timestamps = [p for p in known_timestamps if p[0] is not None]
            known_timestamps.sort(key=lambda p: p[0])
            timestamp = known_timestamps[0][1] if known_timestamps else None
            if len({ts for ts, _ in known_timestamps}) > 1:
                print(
                    f"Warning: attempt {attempt_id} has differing ordering timestamps; "
                    "using the earliest.",
                    file=sys.stderr,
                )

            sorted_events = sorted(events, key=event_sort_key)
            ordering_source = next(
                (e.get("ordering_source") for e in sorted_events if e.get("ordering_source")),
                None,
            )
            attempts.append(
                {
                    "attempt_id": attempt_id,
                    "ordering_timestamp": timestamp,
                    "ordering_source": ordering_source,
                    "events": sorted_events,
                }
            )

        attempts.sort(
            key=lambda a: (
                parse_timestamp(a["ordering_timestamp"]) is None,
                parse_timestamp(a["ordering_timestamp"]) or datetime.max.replace(tzinfo=timezone.utc),
                a["attempt_id"],
            )
        )
        students.append({"learner_key": learner_key, "attempts": attempts})

    # Keep all export-level metadata (cutoff, dataset_version, feature_version,
    # quality_policy, runs, source, source_environment, warning, etc.).
    return {**{k: v for k, v in data.items() if k != "examples"}, "students": students}


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

    with output.open("w", encoding="utf-8") as f:
        json.dump(grouped, f, ensure_ascii=False, indent=2, allow_nan=False)
        f.write("\n")

    students = grouped["students"]
    n_attempts = sum(len(s["attempts"]) for s in students)
    n_events = sum(len(a["events"]) for s in students for a in s["attempts"])
    print(f"Saved: {output}")
    print(f"Students: {len(students)} | Attempts: {n_attempts} | Events: {n_events}")


if __name__ == "__main__":
    main()


# python group_pianokt_by_student.py \
#   output/training-20261106-test.json \
#   output/organized/training-20261106-test.json