#!/usr/bin/env python3
"""Run PianoKT alignment locally and render an inspectable SVG timeline.

Examples:

  .venv/bin/python tests/local_debug/alignment_debug.py \
    --reference /tmp/reference.mid --performance /tmp/performance.mid \
    --events /tmp/performance-events.json --observed-end 7.9 \
    --output /tmp/challenge-alignment

  # Render a previously written result (for example single_payload.json):
  .venv/bin/python tests/local_debug/alignment_debug.py \
    --result tests/local_debug/single_payload.json \
    --output /tmp/old-alignment

Open ``<output>.svg`` in the browser or the IDE preview. Blue marks are score
notes; orange marks are played notes; green joins are candidate matches; red
crosses denote missing/extra notes. The companion ``<output>.json`` preserves
the exact result inspected by the drawing.
"""

from __future__ import annotations

import argparse
import html
import json
import sys
from pathlib import Path


REPOSITORY = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY / "backend"))

from pianokt_backend.alignment.service import align  # noqa: E402


def number(value, default=None):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return default
    return value if value == value and abs(value) != float("inf") else default


def svg_for(result: dict) -> str:
    notes = [note for note in result.get("notes", []) if isinstance(note, dict)]
    times = []
    pitches = []
    for note in notes:
        for key in ("ref_onset_sec", "performance_onset_sec"):
            value = number(note.get(key))
            if value is not None:
                times.append(value)
        for key in ("ref_pitch", "performance_pitch"):
            value = number(note.get(key))
            if value is not None:
                pitches.append(value)

    context = result.get("alignment_context") if isinstance(result.get("alignment_context"), dict) else {}
    observed_end = number(context.get("observed_end_sec"))
    max_time = max([1.0, *(times or [0.0]), observed_end or 0.0])
    min_pitch = max(0, int(min(pitches or [48])) - 3)
    max_pitch = min(127, int(max(pitches or [72])) + 3)
    if min_pitch == max_pitch:
        max_pitch = min(127, min_pitch + 1)

    width, height = 1280, 760
    left, right, top, bottom = 80, 40, 72, 80
    plot_width, plot_height = width - left - right, height - top - bottom

    def x(time):
        return left + max(0.0, min(max_time, time)) / max_time * plot_width

    def y(pitch):
        return top + (max_pitch - pitch) / (max_pitch - min_pitch) * plot_height

    summary = result.get("summary") if isinstance(result.get("summary"), dict) else {}
    diagnostics = result.get("diagnostics") if isinstance(result.get("diagnostics"), dict) else {}
    quality = summary.get("alignment_quality_valid", diagnostics.get("alignment_quality_valid"))
    reason = summary.get("alignment_quality_reason", diagnostics.get("alignment_quality_reason", ""))
    title = (
        f"attempt={result.get('attempt_id', '?')}  run={result.get('alignment_run_id', '?')}  "
        f"quality={'valid' if quality is True else 'REJECTED'}  {reason}"
    )

    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#101418"/>',
        f'<text x="{left}" y="30" fill="#f7fafc" font-family="ui-monospace, monospace" font-size="14">{html.escape(title)}</text>',
        f'<text x="{left}" y="52" fill="#9ca3af" font-family="ui-monospace, monospace" font-size="12">blue = reference, orange = performance, green = match, red × = missing / extra</text>',
    ]

    for pitch in range(min_pitch, max_pitch + 1):
        if pitch % 2:
            continue
        py = y(pitch)
        lines.append(f'<line x1="{left}" y1="{py:.2f}" x2="{width-right}" y2="{py:.2f}" stroke="#28313a" stroke-width="1"/>')
        lines.append(f'<text x="{left-10}" y="{py+4:.2f}" text-anchor="end" fill="#9ca3af" font-family="ui-monospace, monospace" font-size="11">{pitch}</text>')
    for tick in range(int(max_time) + 1):
        px = x(float(tick))
        lines.append(f'<line x1="{px:.2f}" y1="{top}" x2="{px:.2f}" y2="{height-bottom}" stroke="#28313a" stroke-width="1"/>')
        lines.append(f'<text x="{px:.2f}" y="{height-bottom+22}" text-anchor="middle" fill="#9ca3af" font-family="ui-monospace, monospace" font-size="11">{tick}s</text>')

    # Connections go beneath the point marks so a dense chord remains legible.
    for note in notes:
        ref_time, perf_time = number(note.get("ref_onset_sec")), number(note.get("performance_onset_sec"))
        ref_pitch, perf_pitch = number(note.get("ref_pitch")), number(note.get("performance_pitch"))
        if ref_time is not None and perf_time is not None and ref_pitch is not None and perf_pitch is not None:
            color = "#34d399" if note.get("is_correct") else "#f59e0b"
            lines.append(
                f'<line x1="{x(ref_time):.2f}" y1="{y(ref_pitch):.2f}" '
                f'x2="{x(perf_time):.2f}" y2="{y(perf_pitch):.2f}" stroke="{color}" stroke-width="1.5" opacity=".8"/>'
            )

    for note in notes:
        alignment_type = str(note.get("alignment_type", ""))
        ref_time, ref_pitch = number(note.get("ref_onset_sec")), number(note.get("ref_pitch"))
        perf_time, perf_pitch = number(note.get("performance_onset_sec")), number(note.get("performance_pitch"))
        if ref_time is not None and ref_pitch is not None:
            px, py = x(ref_time), y(ref_pitch)
            if alignment_type == "deletion":
                lines.extend(
                    [
                        f'<line x1="{px-5:.2f}" y1="{py-5:.2f}" x2="{px+5:.2f}" y2="{py+5:.2f}" stroke="#fb7185" stroke-width="2"/>',
                        f'<line x1="{px-5:.2f}" y1="{py+5:.2f}" x2="{px+5:.2f}" y2="{py-5:.2f}" stroke="#fb7185" stroke-width="2"/>',
                    ]
                )
            else:
                lines.append(f'<circle cx="{px:.2f}" cy="{py:.2f}" r="4" fill="#60a5fa"/>')
        if perf_time is not None and perf_pitch is not None:
            px, py = x(perf_time), y(perf_pitch)
            if alignment_type == "insertion":
                lines.extend(
                    [
                        f'<line x1="{px-5:.2f}" y1="{py-5:.2f}" x2="{px+5:.2f}" y2="{py+5:.2f}" stroke="#fb7185" stroke-width="2"/>',
                        f'<line x1="{px-5:.2f}" y1="{py+5:.2f}" x2="{px+5:.2f}" y2="{py-5:.2f}" stroke="#fb7185" stroke-width="2"/>',
                    ]
                )
            else:
                lines.append(f'<rect x="{px-3:.2f}" y="{py-3:.2f}" width="6" height="6" fill="#fb923c"/>')

    accuracy = summary.get("accuracy")
    stats = (
        f"expected={summary.get('expected_notes')}  correct={summary.get('correct_notes')}  "
        f"missing={summary.get('missing_notes')}  extra={summary.get('extra_notes')}  accuracy={accuracy}"
    )
    lines.append(f'<text x="{left}" y="{height-28}" fill="#d1d5db" font-family="ui-monospace, monospace" font-size="12">{html.escape(stats)}</text>')
    lines.append("</svg>")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--result", type=Path, help="Existing alignment result JSON to render")
    source.add_argument("--reference", type=Path, help="Reference MIDI; requires --performance")
    parser.add_argument("--performance", type=Path, help="Performance MIDI")
    parser.add_argument("--events", type=Path, help="performance-events.json sidecar")
    parser.add_argument("--attempt-id", default="local-debug")
    parser.add_argument("--reference-origin", type=float, default=0.0)
    parser.add_argument("--observed-end", type=float)
    parser.add_argument("--output", type=Path, required=True, help="Output path without .json/.svg")
    args = parser.parse_args()

    if args.result:
        result = json.loads(args.result.read_text())
    else:
        if not args.performance:
            parser.error("--performance is required with --reference")
        sidecar = json.loads(args.events.read_text()) if args.events else None
        segments = sidecar.get("transportSegments") if isinstance(sidecar, dict) else None
        result = align(
            args.reference,
            args.performance,
            args.attempt_id,
            performance_events=sidecar,
            observed_end_sec=args.observed_end,
            transport_segments=segments,
            reference_origin_sec=args.reference_origin,
        )

    output = args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.with_suffix(".json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    output.with_suffix(".svg").write_text(svg_for(result))
    print(f"result: {output.with_suffix('.json')}")
    print(f"visual: {output.with_suffix('.svg')}")
    print("quality:", result.get("summary", {}).get("alignment_quality_valid"), result.get("summary", {}).get("alignment_quality_reason"))


if __name__ == "__main__":
    main()
