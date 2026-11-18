"""Executable, visual-regression scenarios for time-guided alignment.

The scenarios deliberately call the production ``align`` entry point.  They
are small synthetic takes, but their assertions document the invariant that a
visual report is expected to demonstrate.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import mido

from .service import align


Note = tuple[int, float, float]


@dataclass(frozen=True)
class VisualCase:
    id: str
    title: str
    intent: str
    reference: tuple[Note, ...]
    performance: tuple[Note, ...]
    sidecar: bool = True
    origin: float = 0.0
    observed_end_sec: float | None = None
    mutate_audit: Callable[[dict[str, Any]], None] | None = None


def _notes(*items: Note) -> tuple[Note, ...]:
    return tuple(items)


def _remove_stop(audit: dict[str, Any]) -> None:
    audit['transportSegments'].pop()


def _rewind_transport(audit: dict[str, Any]) -> None:
    # Preserve sequence ordering but make the final note-off travel backwards.
    audit['events'][-1]['playerSongTime'] = audit['events'][0]['playerSongTime'] + 0.01


CASES = (
    VisualCase('on-time', '准时演奏', '基线：所有音符应匹配，timing 接近 0。', _notes((60, 0, .12), (62, .5, .12), (64, 1, .12)), _notes((60, 0, .12), (62, .5, .12), (64, 1, .12))),
    VisualCase('constant-late', '固定晚 120 ms', '有 sidecar 时必须保留 +120 ms，不能被全局 offset 消除。', _notes((60, 0, .12), (62, .5, .12), (64, 1, .12)), _notes((60, .12, .12), (62, .62, .12), (64, 1.12, .12))),
    VisualCase('dragging', '逐渐拖拍', '延迟从 20 ms 累积到 260 ms，保留局部节奏漂移。', _notes((60, 0, .12), (62, .5, .12), (64, 1, .12), (65, 1.5, .12)), _notes((60, .02, .12), (62, .58, .12), (64, 1.16, .12), (65, 1.76, .12))),
    VisualCase('early', '提前演奏', '负 timing 是有效表现信息，不能被钳制成零。', _notes((60, .2, .12), (62, .7, .12), (64, 1.2, .12)), _notes((60, .08, .12), (62, .58, .12), (64, 1.08, .12))),
    VisualCase('missed-note', '漏音', '中间参考音应是 deletion，而不是被后续音错配。', _notes((60, 0, .12), (62, .5, .12), (64, 1, .12)), _notes((60, .02, .12), (64, 1.02, .12))),
    VisualCase('extra-note', '多音', '多出的音应是 insertion，而不是吞掉参考音。', _notes((60, 0, .12), (64, 1, .12)), _notes((60, .02, .12), (61, .48, .12), (64, 1.02, .12))),
    VisualCase('extra-note-in-chord', '和弦内：正确音加一个多音', 'C、E 应分别 match；同一和弦中多按的 C# 必须单独成为 insertion。', _notes((60, 0, .12), (64, 0, .12)), _notes((60, .02, .12), (61, .02, .12), (64, .02, .12))),
    VisualCase('wrong-pitch', '错音', '同一时间窗内的错误音应显示为 substitution/mismatch。', _notes((60, 0, .12), (62, .5, .12)), _notes((60, .02, .12), (63, .52, .12))),
    VisualCase('seeked-window', '从第 10 秒开始的窗口', '绝对 transport time 必须减去 snapshot origin；图中仍保留绝对值。', _notes((60, 0, .12), (64, .5, .12)), _notes((60, .1, .12), (64, .6, .12)), origin=10),
    VisualCase('repeated-late-phrase', '重复乐句不得跨窗口匹配', 'reference 包含 35 秒后的相同乐句，但 observed window 只能看到开头。', _notes((60, 0, .12), (62, .5, .12), (64, 1, .12), (60, 35, .12), (62, 35.5, .12), (64, 36, .12)), _notes((60, .1, .12), (62, .6, .12), (64, 1.1, .12)), observed_end_sec=1.2),
    VisualCase('incomplete-transport', '不完整 transport cycle', '没有 stop 的侧录必须被质量门禁拒绝。', _notes((60, 0, .12)), _notes((60, .02, .12)), mutate_audit=_remove_stop),
    VisualCase('rewound-transport', '倒退的 transport', 'seek/loop 导致时间倒退时，不应被重排为看似合理的演奏。', _notes((60, 0, .12), (62, .5, .12)), _notes((60, .02, .12), (62, .52, .12)), mutate_audit=_rewind_transport),
)


def write_midi(path: Path, notes: tuple[Note, ...]) -> None:
    midi = mido.MidiFile(ticks_per_beat=480)
    track = mido.MidiTrack(); midi.tracks.append(track)
    track.append(mido.MetaMessage('set_tempo', tempo=500_000, time=0))
    events: list[tuple[int, int, int, str, int]] = []
    for order, (pitch, onset, duration) in enumerate(notes):
        events.extend(((round(onset * 960), 1, order, 'on', pitch), (round((onset + duration) * 960), 0, order, 'off', pitch)))
    events.sort()
    previous = 0
    for tick, _, _, kind, pitch in events:
        track.append(mido.Message('note_on' if kind == 'on' else 'note_off', note=pitch, velocity=90 if kind == 'on' else 0, time=tick - previous))
        previous = tick
    midi.save(path)


def make_sidecar(notes: tuple[Note, ...], origin: float) -> dict[str, Any]:
    wire: list[tuple[float, int, int, str, int]] = []
    for order, (pitch, onset, duration) in enumerate(notes):
        wire.extend(((onset, 0, order, 'note_on', pitch), (onset + duration, 1, order, 'note_off', pitch)))
    wire.sort()
    events = [
        {'sequence': index, 'type': kind, 'playerSongTime': origin + time, 'performanceNowTimestamp': 1_000 + time * 1_000, 'pitch': pitch, 'velocity': 90 if kind == 'note_on' else 0}
        for index, (time, _, _, kind, pitch) in enumerate(wire)
    ]
    end = origin + max((onset + duration for _, onset, duration in notes), default=0)
    return {'schemaVersion': 1, 'events': events, 'transportSegments': [{'sequence': 0, 'type': 'start', 'playerSongTime': origin, 'performanceNowTimestamp': 0}, {'sequence': 1, 'type': 'stop', 'playerSongTime': end, 'performanceNowTimestamp': 1_000 + (end - origin) * 1_000}]}


def run_case(case: VisualCase, directory: Path) -> dict[str, Any]:
    reference, performance = directory / f'{case.id}-reference.mid', directory / f'{case.id}-performance.mid'
    write_midi(reference, case.reference); write_midi(performance, case.performance)
    audit = make_sidecar(case.performance, case.origin) if case.sidecar else None
    if audit is not None and case.mutate_audit:
        case.mutate_audit(audit)
    return align(reference, performance, case.id, performance_events=audit, transport_segments=audit['transportSegments'] if audit else None, observed_end_sec=case.observed_end_sec, reference_origin_sec=case.origin)
