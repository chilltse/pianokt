"""Join canonical Reference features with responses by source identity."""
from typing import Any
import pandas as pd
from .feature_extraction import KC_FEATURE_NAMES, ReferenceFeatures, ReferenceGroupingMismatch
from .labels import classify_matched_note


def validate_reference_alignment_consistency(features, frame):
    if not isinstance(features, ReferenceFeatures):
        raise ReferenceGroupingMismatch("Legacy events API unsupported: supply canonical ReferenceFeatures and identity-bearing Alignment rows")
    required = {'alignment_type', 'ref_id', 'reference_chord_index', 'ref_pitch',
                'ref_track', 'ref_onset_tick', 'ref_onset_sec', 'performance_id'}
    if required - set(frame.columns):
        raise ReferenceGroupingMismatch(f"Missing identity columns: {sorted(required - set(frame.columns))}")
    expected = {identity: (index, note) for index, group in enumerate(features.groups)
                for identity, note in group.items()}
    seen = set()
    performances = set()
    for row in frame.to_dict('records'):
        identity = row['ref_id']
        perf = row['performance_id']
        if pd.notna(perf):
            if perf in performances:
                raise ReferenceGroupingMismatch(f"Performance consumed twice: {perf}")
            performances.add(perf)
        kind = row['alignment_type']
        if kind == 'insertion':
            if pd.notna(identity) or pd.notna(row['reference_chord_index']):
                raise ReferenceGroupingMismatch(f"Insertion has Reference identity: {identity}")
            continue
        if kind not in ('match', 'deletion') or identity not in expected or identity in seen:
            raise ReferenceGroupingMismatch(f"Unknown/duplicate Reference ref_id={identity}")
        seen.add(identity)
        index, note = expected[identity]
        for key, value in [('reference_chord_index', index), ('ref_pitch', note.pitch),
                           ('ref_track', note.track), ('ref_onset_tick', note.onset_tick),
                           ('ref_onset_sec', note.onset_sec)]:
            actual = row[key]
            equal = pd.isna(actual) if value is None else pd.notna(actual) and actual == value
            if not equal:
                raise ReferenceGroupingMismatch(f"group={index} ref_id={identity} {key}: expected={value} actual={actual}")
    if seen != expected.keys():
        raise ReferenceGroupingMismatch(f"Missing Reference members: {sorted(expected.keys() - seen)}")


def build_attempt_events_data(features, df, timing_threshold_ms=150,
                              insertion_attach_window_sec=.25) -> dict[str, Any]:
    validate_reference_alignment_consistency(features, df)
    rows = {row['ref_id']: row for row in df.to_dict('records') if row['alignment_type'] != 'insertion'}
    events = features.events
    extras = [[] for _ in events]
    for row in df.to_dict('records'):
        onset = row.get('performance_onset_sec_global_aligned')
        pitch = row.get('performance_pitch')
        if row['alignment_type'] != 'insertion' or not events or pd.isna(onset) or pd.isna(pitch):
            continue
        i = min(range(len(events)), key=lambda i: abs(events[i]['onset'] - onset))
        if abs(events[i]['onset'] - onset) <= insertion_attach_window_sec:
            extras[i].append(int(round(pitch)))
    notes, timings, deltas, labels = [], [], [], []
    for i, event in enumerate(events):
        event_responses = []
        for hand in event['targets']:
            responses = []
            for pitch, identities in hand.items():
                outcomes = []
                for identity in identities:
                    row = rows[identity]
                    if row['alignment_type'] == 'deletion':
                        outcome = (1, None, None)
                    else:
                        deviation = row['timing_deviation_global_aligned']
                        outcome = (classify_matched_note(row, 'ref_pitch', 'performance_pitch',
                                   'timing_deviation_global_aligned', timing_threshold_ms),
                                   int(round(deviation * 1000)) if pd.notna(deviation) else None,
                                   int(round(row['performance_pitch'] - pitch)) if pd.notna(row['performance_pitch']) else None)
                    outcomes.append(outcome)
                # A single schema pitch cannot represent conflicting raw responses.
                if any(outcome != outcomes[0] for outcome in outcomes[1:]):
                    raise ReferenceGroupingMismatch(f"Conflicting duplicate pitch group={event['group']} ref_ids={identities}")
                responses.append(outcomes[0])
            event_responses.append(responses)
        notes.append([[r[0] for r in hand] for hand in event_responses])
        timings.append([[r[1] for r in hand] for hand in event_responses])
        deltas.append([[r[2] for r in hand] for hand in event_responses])
        labels.append(int(any(code for hand in notes[-1] for code in hand) or bool(extras[i])))
    data = dict(kc_feature_names=list(features.kc_feature_names), onset_seconds=[e['onset'] for e in events],
                onset_ids=[f"o{e['group']}" for e in events],
                reference_chord_indices=[e['group'] for e in events],
                reference_group_note_ids=[list(features.groups[e['group']]) for e in events],
                ref_note_ids=[[[list(ids) for ids in hand.values()] for hand in e['targets']] for e in events],
                pitches=[e['pitches'] for e in events], intervals_seconds=[e['intervals'] for e in events],
                kc_vectors=[e['vector'] for e in events], reject_reason=dict(event=labels, note=notes),
                timing_offset_ms=timings, pitch_offset_semitones=deltas, extra_notes=[sorted(x) for x in extras])
    from ..pipeline.attempts import validate_events
    validate_events(data)
    return data
