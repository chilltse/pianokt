"""Catalog identity and exercise identity shared by API, activity and exports."""
from uuid import UUID
from fastapi import HTTPException
from .contracts import stable_id

CONTENT_FIELDS = ('segment_id', 'segment_index', 'midi_sha256')


def validate_identity(source, song_id, segment_id, segment_index, midi_sha256):
    values = (segment_id, segment_index, midi_sha256)
    if source != 'builtin':
        if any(value is not None for value in values):
            raise ValueError('Official MIDI identity is only valid for builtin songs')
        return song_id
    missing=[key for key,value in zip(CONTENT_FIELDS,values) if value is None]
    if missing:
        raise ValueError('Official practice is missing original catalog identity: '+', '.join(missing))
    try:
        UUID(str(song_id))
        UUID(str(segment_id))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError('Official song and segment IDs must be UUIDs') from exc
    import re
    if not isinstance(segment_index, int) or isinstance(segment_index, bool) or segment_index < 1 or not isinstance(midi_sha256, str) or not re.fullmatch('[0-9a-f]{64}', midi_sha256):
        raise ValueError('Official practice requires a segment index and original MIDI SHA-256')
    return str(UUID(str(song_id)))


def check_catalog_identity(c, identity):
    if identity.song_source != 'builtin':
        return
    row = c.execute('''select m.midi_sha256 from public.piano_song_midis m
        join public.piano_song_segments s on s.segment_id=m.segment_id
        where m.song_id=%s and m.segment_id=%s and s.segment_index=%s and m.midi_sha256=%s''',
        (UUID(identity.song_id), identity.segment_id, identity.segment_index, identity.midi_sha256)).fetchone()
    if not row:
        raise HTTPException(422, 'Song/segment/MIDI hash does not match the catalog')


def exercise_identity(source, song_id, segment_id, midi_sha256, settings):
    return 'exercise-' + stable_id(source, str(song_id), str(segment_id) if segment_id else None, midi_sha256,
        {key: settings.get(key) for key in ('assigned_range', 'left', 'right', 'transpose', 'waiting', 'tracks')})


def assert_export_identity(result, context):
    """Never train on one segment's labels under another segment's identity."""
    fields = ('song_id', *CONTENT_FIELDS)
    if context.get('song_source') == 'builtin' or any(context.get(key) is not None or result.get(key) is not None for key in CONTENT_FIELDS):
        validate_identity('builtin', *(context.get(key) for key in fields))
        if any(str(context.get(key)) != str(result.get(key)) for key in fields):
            raise ValueError('Original MIDI/segment identity differs between attempt and alignment result')
