"""Reference-only artifacts, without creating a synthetic performance or attempt."""
from dataclasses import asdict
from pathlib import Path

from ...contracts import check_midi, digest
from ..hands import parse_midi_song_for_hand_inference, parser_infer_hands
from ..time_guided import read_midi_notes, reference_note_id
from . import extract_reference_features
from ..reference_grouping import group_reference_onsets, load_reference_grouping_policy


def extract_midi_reference(path: Path, *, kc_labeler_version=None, reference_grouping_version=None):
    check_midi(path)
    notes = read_midi_notes(str(path))
    if not notes:
        raise ValueError(f"No playable notes: {path}")
    policy = load_reference_grouping_policy(reference_grouping_version)
    tolerance = policy["onset_grouping_ms"] / 1000
    chords = group_reference_onsets(notes, policy["version"])
    mapping = parser_infer_hands(parse_midi_song_for_hand_inference(path), "Reference MIDI")
    features = extract_reference_features(chords, mapping, kc_labeler_version=kc_labeler_version)
    events = features.events
    selected = set(mapping.values()) - {None}
    return {
        "schema_version": 1,
        "artifact_type": "reference_features",
        "reference_grouping_version": policy["version"],
        "kc_labeler_version": features.kc_labeler_version,
        "midi_sha256": digest(path.read_bytes()),
        "source_file": path.name,
        "config": {"chord_onset_tolerance_sec": tolerance},
        "hand_mapping": mapping,
        "reference_notes_count": len(notes),
        "reference_events_count": len(events),
        "excluded_note_tracks": sorted({n.track for n in notes if n.track not in selected}),
        "notes": [{"ref_id": reference_note_id(n), **asdict(n)} for n in notes],
        "events_data": {
            "kc_feature_names": list(features.kc_feature_names),
            "onset_seconds": [e["onset"] for e in events],
            "onset_ids": [f"o{e['group']}" for e in events],
            "reference_chord_indices": [e["group"] for e in events],
            "reference_group_note_ids": [list(features.groups[e["group"]]) for e in events],
            "ref_note_ids": [[[list(ids) for ids in hand.values()] for hand in e["targets"]] for e in events],
            "pitches": [e["pitches"] for e in events],
            "intervals_seconds": [e["intervals"] for e in events],
            "kc_vectors": [e["vector"] for e in events],
        },
    }
