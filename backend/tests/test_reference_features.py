"""KC 1.2 canonical grouping, identity failures and immutable version boundaries."""
import pandas as pd
import pytest
from pianokt_backend.alignment.time_guided import MidiNote, group_chords, rows_from_operations
from pianokt_backend.alignment.feature_extraction import extract_reference_features, ReferenceGroupingMismatch
from pianokt_backend.alignment.events import build_attempt_events_data
from pianokt_backend.alignment.service import _apply_timing
from pianokt_backend.pipeline.kc_versions import dataset_kc_version


def note(t, seq, pitch=60, track=0):
    return MidiNote(t, .1, pitch, 90, track, 0, seq * 100, seq)


def fixture(notes=None):
    chords = group_chords(notes or [note(0, 0), note(.02, 1, 64), note(.04, 2, 67)], .03)
    features = extract_reference_features(chords, {'left': None, 'right': 0})
    frame = pd.DataFrame(rows_from_operations(chords, chords, [('match', i, i) for i in range(len(chords))]))
    return features, _apply_timing(frame, 0)


def test_anchor_median_and_kc():
    features, frame = fixture()
    data = build_attempt_events_data(features, frame)
    assert data['onset_seconds'] == [.01, .04]
    assert data['pitches'] == [[[], [60, 64]], [[], [67]]]
    assert data['kc_vectors'] == [[False, 2, False, None, 2, None], [False, 1, False, 10, 1, 1]]
    assert data['reject_reason']['event'] == [0, 0]
    assert len(group_chords([note(0, 0), note(.03, 1)], .03)) == 1


@pytest.mark.parametrize('field,value', [('ref_id', 'wrong'), ('reference_chord_index', 1),
    ('ref_pitch', 61), ('ref_track', 7), ('ref_onset_tick', 9), ('ref_onset_sec', .001)])
def test_corrupted_identity_fails(field, value):
    features, frame = fixture()
    frame.loc[0, field] = value
    with pytest.raises(ReferenceGroupingMismatch):
        build_attempt_events_data(features, frame)


@pytest.mark.parametrize('duplicate', [True, False])
def test_missing_or_duplicate_row_fails(duplicate):
    features, frame = fixture()
    frame = pd.concat([frame, frame.iloc[:1]]) if duplicate else frame.iloc[1:]
    with pytest.raises(ReferenceGroupingMismatch):
        build_attempt_events_data(features, frame)


def test_duplicate_pitch_conflict_and_unselected_track():
    notes = [note(0, 0), note(0, 1), note(0, 2, 60, 1), note(0, 3, 80, 2)]
    chords = group_chords(notes, .03)
    features = extract_reference_features(chords, {'left': 0, 'right': 1})
    frame = _apply_timing(pd.DataFrame(rows_from_operations(chords, chords, [('match', 0, 0)])), 0)
    assert build_attempt_events_data(features, frame)['pitches'] == [[[60], [60]]]
    frame.loc[0, 'alignment_type'] = 'deletion'
    with pytest.raises(ReferenceGroupingMismatch, match='Conflicting duplicate'):
        build_attempt_events_data(features, frame)


def test_insertion_once_and_error_codes():
    features, frame = fixture()
    extra = frame.iloc[0].copy()
    extra['alignment_type'] = 'insertion'
    extra['ref_id'] = None
    extra['reference_chord_index'] = None
    extra['performance_id'] = 'extra'
    extra['performance_pitch'] = 80
    frame.loc[0, 'performance_pitch'] = 61
    frame.loc[1, 'alignment_type'] = 'deletion'
    frame.loc[2, 'timing_deviation_global_aligned'] = .2
    data = build_attempt_events_data(features, pd.concat([frame, pd.DataFrame([extra])]))
    assert data['reject_reason']['note'] == [[[], [2, 1]], [[], [3]]]
    assert data['extra_notes'] == [[80], []]
    assert data['reject_reason']['event'] == [1, 1]


def test_versions_never_upgrade_legacy_or_mix():
    assert dataset_kc_version([{}]) == 'v1.1'
    assert dataset_kc_version([{'kc_labeler_version': '1.2.0'}]) == '1.2.0'
    with pytest.raises(ValueError, match='Mixed KC'):
        dataset_kc_version([{}, {'kc_labeler_version': '1.2.0'}])


def test_version_changes_run_and_cache(tmp_path, monkeypatch):
    from pianokt_backend.alignment import service
    from pianokt_backend.worker import _is_current_result
    from pianokt_backend.contracts import AlignmentConfig, digest
    path = tmp_path / 'midi'
    path.write_bytes(b'stable bytes')
    current = service.alignment_run_id(path, path, 'attempt')
    from pianokt_backend.alignment.feature_extraction import registry
    from dataclasses import replace
    monkeypatch.setitem(registry._REGISTRY, 'test-version',
                        replace(registry.get_extractor(), version='test-version'))
    assert service.alignment_run_id(path, path, 'attempt', kc_labeler_version='test-version') != current
    row = dict(id='attempt', reference_hash=digest(path.read_bytes()), performance_hash=digest(path.read_bytes()))
    result = dict(attempt_id='attempt', alignment_run_id=current, score_version=row['reference_hash'],
                  performance_hash=row['performance_hash'], matcher_version=service.matcher_version(),
                  config=AlignmentConfig().identity, alignment_context=dict(reference_origin_sec=0,
                  observed_end_sec=None, transport_segments_count=None, event_audit_supplied=False))
    assert not _is_current_result(result, row, AlignmentConfig(), current, None, 0, None, None)
    result['kc_labeler_version'] = '1.2.0'
    assert _is_current_result(result, row, AlignmentConfig(), current, None, 0, None, None)
    row.update(song_id='song',metadata=dict(segment_id='segment',segment_index=1,midi_sha256='a'*64))
    assert not _is_current_result(result,row,AlignmentConfig(),current,None,0,None,None)
    result.update(song_id='song',**row['metadata'])
    assert _is_current_result(result,row,AlignmentConfig(),current,None,0,None,None)
    result['midi_sha256']='b'*64
    assert not _is_current_result(result,row,AlignmentConfig(),current,None,0,None,None)



def test_registry_default_explicit_and_unknown():
    from pianokt_backend.alignment.feature_extraction import available_versions, resolve_kc_version
    assert available_versions() == ('1.2.0',)
    assert resolve_kc_version() == '1.2.0'
    features, frame = fixture()
    chords = group_chords([note(0, 0), note(.02, 1, 64), note(.04, 2, 67)], .03)
    explicit = extract_reference_features(chords, {'left': None, 'right': 0}, kc_labeler_version='1.2.0')
    assert explicit == features
    for version in ('1.3.0', 'v1.1', ''):
        with pytest.raises(ValueError, match='Unsupported kc_labeler_version'):
            extract_reference_features(chords, {}, kc_labeler_version=version)


def test_registered_dimensions_and_metadata_are_enforced(monkeypatch):
    from dataclasses import replace
    from pianokt_backend.alignment.feature_extraction import registry
    base = registry.get_extractor()
    def seven_dimensions(chords, hands):
        features = base.extract(chords, hands)
        return replace(features, kc_labeler_version='test-seven',
                       kc_feature_names=base.feature_names + ('test_feature',),
                       events=tuple({**e, 'vector': e['vector'] + [42]} for e in features.events))
    monkeypatch.setitem(registry._REGISTRY, 'test-seven',
        registry.FeatureExtractor('test-seven', base.feature_names + ('test_feature',), seven_dimensions))
    chords = group_chords([note(0, 0)], .03)
    features = extract_reference_features(chords, {'right': 0}, kc_labeler_version='test-seven')
    frame = _apply_timing(pd.DataFrame(rows_from_operations(chords, chords, [('match', 0, 0)])), 0)
    data = build_attempt_events_data(features, frame)
    assert len(data['kc_feature_names']) == len(data['kc_vectors'][0]) == 7
    # Changing the registry default does not affect an explicitly pinned version.
    monkeypatch.setattr(registry, 'DEFAULT_KC_LABELER_VERSION', 'test-seven')
    assert extract_reference_features(chords, {'right': 0}).kc_labeler_version == 'test-seven'
    assert extract_reference_features(chords, {'right': 0}, kc_labeler_version='1.2.0').kc_labeler_version == '1.2.0'
    monkeypatch.setitem(registry._REGISTRY, 'broken', registry.FeatureExtractor('broken', base.feature_names, base.extract))
    with pytest.raises(ReferenceGroupingMismatch, match='metadata'):
        extract_reference_features(chords, {}, kc_labeler_version='broken')


def test_exported_event_ids_preserve_duplicate_and_unselected_members():
    chords = group_chords([note(0, 0), note(0, 1), note(0, 2, 60, 1), note(0, 3, 80, 2)], .03)
    features = extract_reference_features(chords, {'left': 0, 'right': 1})
    frame = _apply_timing(pd.DataFrame(rows_from_operations(chords, chords, [('match', 0, 0)])), 0)
    data = build_attempt_events_data(features, frame)
    assert data['onset_ids'] == ['o0']
    assert data['reference_chord_indices'] == [0]
    assert data['ref_note_ids'] == [[ [['r0_n0', 'r0_n1']], [['r1_n2']] ]]
    assert set(data['reference_group_note_ids'][0]) == {'r0_n0', 'r0_n1', 'r1_n2', 'r2_n3'}


@pytest.mark.parametrize('corrupt', ['length', 'shape', 'unknown', 'duplicate'])
def test_event_identity_validation_rejects_corruption(corrupt):
    from pianokt_backend.pipeline.attempts import validate_events
    features, frame = fixture()
    data = build_attempt_events_data(features, frame)
    if corrupt == 'length': data['onset_ids'].pop()
    elif corrupt == 'shape': data['ref_note_ids'][0][1].pop()
    elif corrupt == 'unknown': data['ref_note_ids'][0][1][0] = ['unknown']
    elif corrupt == 'duplicate': data['reference_group_note_ids'][1].append(data['reference_group_note_ids'][0][0])
    with pytest.raises(ValueError):
        validate_events(data)


def test_nullable_integer_serialization_never_truncates():
    import json
    from pianokt_backend.alignment.serialization import alignment_records, INTEGER_NOTE_FIELDS
    frame = pd.DataFrame([{k: 2 for k in INTEGER_NOTE_FIELDS}, {k: None for k in INTEGER_NOTE_FIELDS}])
    frame['ref_onset_sec'] = [.125, None]
    records = json.loads(json.dumps(alignment_records(frame), allow_nan=False))
    assert all(type(records[0][k]) is int and records[1][k] is None for k in INTEGER_NOTE_FIELDS)
    assert records[0]['ref_onset_sec'] == .125
    frame.loc[0, 'ref_pitch'] = 50.5
    with pytest.raises(ReferenceGroupingMismatch, match='Non-integral ref_pitch'):
        alignment_records(frame)
