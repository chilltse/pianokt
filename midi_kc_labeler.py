#!/usr/bin/env python3
"""Onset-only piano features. Python 3.9+; pip install mido.

python midi_onset_labeler.py song.mid --right-tracks 0 --left-tracks 1
Track indices are zero-based INCLUDING metadata tracks. Inspect with --inspect.
No sustain/note-off state, pitch-based hand guessing or onset quantization.
"""
import argparse
import bisect
import csv
import json
from pathlib import Path
from functools import lru_cache
import mido

KEY_X = (0, .5, 1, 1.5, 2, 3, 3.5, 4, 4.5, 5, 5.5, 6)
FEATURES = ['has_black_key', 'simultaneous_note_count', 'both_hands',
            'onset_interval_sec', 'change_count', 'mean_move']


def key_x(pitch):
    octave, pc = divmod(pitch, 12)
    return 7 * octave + KEY_X[pc]


def match_one_hand(previous, current):
    """Same objective as supplied code; DP replaces exponential subset enumeration."""
    a, b = set(previous), set(current)
    common = sorted(a & b)
    old, new = sorted(a - b), sorted(b - a)
    # Match every element of the smaller list to a subsequence of the larger.
    small, large = (old, new) if len(old) <= len(new) else (new, old)
    reverse = len(old) > len(new)

    @lru_cache(None)
    def solve(i, j):
        if i == len(small):
            return 0.0, ()
        if len(large) - j < len(small) - i:
            return float('inf'), ()
        cost, tail = solve(i + 1, j + 1)
        pair = (large[j], small[i]) if reverse else (small[i], large[j])
        take = (abs(key_x(pair[1]) - key_x(pair[0])) + cost, (pair,) + tail)
        skip = solve(i, j + 1)
        return take if take[0] <= skip[0] else skip

    total, moved = solve(0, 0)
    pairs = [(p, p) for p in common] + list(moved)
    return dict(change_count=len(b-a), pairs=pairs,
                added=sorted(b-{q for _, q in pairs}),
                released=sorted(a-{p for p, _ in pairs}),
                total_move=total, matched_count=len(pairs),
                mean_move=total / len(pairs) if pairs else None)


def read_midi(path):
    mid = mido.MidiFile(path)
    if mid.type == 2:
        raise ValueError('Type 2 contains independent timelines; split it first.')
    if mid.ticks_per_beat <= 0:
        raise ValueError('SMPTE time division is unsupported; convert to PPQ first.')
    notes, tempos, tracks = [], {}, []
    for index, track in enumerate(mid.tracks):
        tick, pitches, channels, name = 0, [], set(), ''
        for msg in track:
            tick += msg.time
            if msg.type == 'track_name':
                name = msg.name
            elif msg.type == 'set_tempo':
                if msg.tempo <= 0:
                    raise ValueError('Tempo must be positive.')
                if tick in tempos and tempos[tick] != msg.tempo:
                    raise ValueError(f'Conflicting tempo at tick {tick}')
                tempos[tick] = msg.tempo
            elif msg.type == 'note_on' and msg.velocity > 0:
                notes.append(dict(tick=tick, track=index, channel=msg.channel,
                                  pitch=msg.note, velocity=msg.velocity))
                pitches.append(msg.note)
                channels.add(msg.channel)
        tracks.append(dict(index=index, name=name, note_on_count=len(pitches),
                           channels=sorted(channels),
                           pitch_range=[min(pitches), max(pitches)] if pitches else None))
    tempos.setdefault(0, 500000)
    ticks = sorted(tempos)
    seconds = [0.0]
    for prev, tick in zip(ticks, ticks[1:]):
        seconds.append(seconds[-1] + (tick-prev)*tempos[prev]/1e6/mid.ticks_per_beat)

    def to_seconds(tick):
        i = bisect.bisect_right(ticks, tick)-1
        return seconds[i] + (tick-ticks[i])*tempos[ticks[i]]/1e6/mid.ticks_per_beat

    return notes, tracks, to_seconds, mid.ticks_per_beat


def label_events(notes, to_seconds, hand_map):
    groups = {}
    for note in notes:
        if note['track'] not in hand_map:
            raise ValueError(f"Unassigned note track {note['track']}; supply a hand mapping.")
        groups.setdefault(note['tick'], []).append(note)
    history, history_ticks = {'L': [], 'R': []}, {'L': None, 'R': None}
    rows, details, previous_sec = [], [], None
    for event_id, (tick, group) in enumerate(sorted(groups.items())):
        sec = to_seconds(tick)
        current = {h: sorted({n['pitch'] for n in group if hand_map[n['track']] == h})
                   for h in ('L', 'R')}
        per_hand = {}
        for hand in ('L', 'R'):
            if current[hand]:
                result = match_one_hand(history[hand], current[hand])
                result['previous_onset_tick'] = history_ticks[hand]
                per_hand[hand] = result
                history[hand], history_ticks[hand] = current[hand], tick
        matched = sum(v['matched_count'] for v in per_hand.values())
        distance = sum(v['total_move'] for v in per_hand.values())
        row = dict(event_id=event_id, onset_tick=tick, onset_sec=round(sec, 9),
                   left_pitches=current['L'], right_pitches=current['R'],
                   has_black_key=any(n['pitch'] % 12 in (1, 3, 6, 8, 10) for n in group),
                   simultaneous_note_count=len(current['L'])+len(current['R']),
                   both_hands=bool(current['L'] and current['R']),
                   onset_interval_sec=round(sec-previous_sec, 9) if previous_sec is not None else None,
                   change_count=sum(v['change_count'] for v in per_hand.values()),
                   mean_move=distance/matched if matched else None,
                   raw_note_on_count=len(group), matched_count=matched)
        rows.append(row)
        details.append(dict(**row, hand_matches=per_hand, source_notes=group))
        previous_sec = sec
    return rows, details


def track_set(value):
    return {int(x.strip()) for x in value.split(',') if x.strip()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('midi', type=Path)
    parser.add_argument('--inspect', action='store_true')
    parser.add_argument('--left-tracks', type=track_set, default=set())
    parser.add_argument('--right-tracks', type=track_set, default=set())
    parser.add_argument('--output-prefix', type=Path)
    args = parser.parse_args()
    notes, tracks, clock, ppq = read_midi(args.midi)
    if args.inspect:
        print(json.dumps(tracks, indent=2, ensure_ascii=False))
        return
    if args.left_tracks & args.right_tracks:
        parser.error('A track cannot be assigned to both hands.')
    hand_map = {**{i: 'L' for i in args.left_tracks}, **{i: 'R' for i in args.right_tracks}}
    if set(hand_map) - set(range(len(tracks))):
        parser.error('Track index out of range.')
    if not notes:
        parser.error('No positive-velocity note_on events.')
    try:
        rows, details = label_events(notes, clock, hand_map)
    except ValueError as exc:
        parser.error(str(exc))
    prefix = args.output_prefix or args.midi.with_suffix('')
    prefix.parent.mkdir(parents=True, exist_ok=True)
    csv_path = Path(str(prefix) + '_labels.csv')
    json_path = Path(str(prefix) + '_labels.json')
    with csv_path.open('w', newline='', encoding='utf-8-sig') as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        for row in rows:
            writer.writerow({k: json.dumps(v) if isinstance(v, (list, bool)) else v for k, v in row.items()})
    payload = dict(source=args.midi.name, ticks_per_beat=ppq, tracks=tracks,
                   hand_map=hand_map, feature_order=FEATURES,
                   definitions=dict(grouping='exact onset tick', duration='ignored',
                   hand_history='last nonempty onset of each hand; never reset by inactivity',
                   note_count='unique (hand, pitch); raw_note_on_count retains duplicates',
                   distance_unit='supplied KEY_X white-key coordinate',
                   first_interval=None, first_hand_change='all current pitches count as added',
                   first_hand_move=None, mean_denominator='all matched pairs including unchanged pitches'),
                   event_count=len(rows), raw_note_on_count=len(notes), events=details)
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False)+'\n', encoding='utf-8')
    print(f'{len(notes)} note-ons -> {len(rows)} onset events\n{csv_path}\n{json_path}')


if __name__ == '__main__':
    main()
