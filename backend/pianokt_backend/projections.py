"""Rebuildable product projections; client outcome and aligned grade stay distinct."""
from fastapi import HTTPException
from .upload_contracts import Finalize, validate_finalization


def project_challenge(c, row, body):
    existing_recording = c.execute('select user_id from public.challenge_recordings where id=%s', (row['id'],)).fetchone()
    if existing_recording and str(existing_recording['user_id']) != str(row['user_id']):
        raise HTTPException(409, 'Recording projection ownership conflict')
    events = body.activity.events
    terminal = events[-1]
    settings = row['metadata'].get('settings', {})
    quality = row.get('summary') or {}
    aligned = row['status'] == 'READY' and quality.get('alignment_quality_valid') is True and quality.get('accuracy') is not None
    score_source = ('aligned' if aligned else 'rejected') if row['status'] == 'READY' else 'pending'
    score = float(quality['accuracy']) * 100 if aligned else 0
    c.execute('''insert into public.challenge_recordings(id,user_id,song_source,song_id,song_title,duration_sec,midi_storage_path,midi_keyboard_used,accuracy_pct,difficulty,client_accuracy_pct,score_source,source_environment)
        values(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        on conflict(id) do update set song_source=excluded.song_source,song_id=excluded.song_id,
        song_title=excluded.song_title,duration_sec=excluded.duration_sec,midi_storage_path=excluded.midi_storage_path,
        midi_keyboard_used=excluded.midi_keyboard_used,accuracy_pct=excluded.accuracy_pct,difficulty=excluded.difficulty,
        client_accuracy_pct=excluded.client_accuracy_pct,score_source=excluded.score_source,source_environment=excluded.source_environment''',
        (row['id'], row['user_id'], row['song_source'], row['song_id'], row.get('song_title'),
         str(row['metadata']['duration_sec']), f"gcs:{row['id']}", bool(settings.get('midi_keyboard_used', False)),
         str(score), str(settings.get('client_difficulty') or 0),
         str(settings['client_accuracy_pct']) if settings.get('client_accuracy_pct') is not None else None, score_source, row['source_environment']))
    existing = c.execute('select user_id,challenge_recording_id from public.user_play_logs where session_id=%s for update', (row['id'],)).fetchone()
    if existing and (str(existing['user_id']) != str(row['user_id']) or
                     (existing['challenge_recording_id'] and str(existing['challenge_recording_id']) != str(row['id']))):
        raise HTTPException(409, 'Session projection ownership conflict')
    client_success = terminal.metadata.get('success')
    client_success = client_success if isinstance(client_success, bool) else None
    exit_status = 'abandoned' if terminal.event_type == 'exited' else ('succeeded' if client_success is True else 'failed' if client_success is False else 'unknown')
    c.execute('''insert into public.user_play_logs(session_id,user_id,song_id,exercise_id,play_mode,
        days_since_signup,time_playing,song_time_sec,challenge_recording_id,is_played_in_full,exit_status,
        started_at,ended_at,events_count,completion_reason,client_success,projection_version)
        values(%s,%s,%s,%s,'challenge',(extract(epoch from (%s::timestamptz-public.piano_registration_time(%s)))/86400.0),
        %s::numeric,%s::numeric,%s,%s,%s,%s,%s,%s,%s,%s,2)
        on conflict(session_id) do update set song_id=excluded.song_id,exercise_id=excluded.exercise_id,
        started_at=excluded.started_at,days_since_signup=excluded.days_since_signup,time_playing=excluded.time_playing,
        song_time_sec=excluded.song_time_sec,challenge_recording_id=excluded.challenge_recording_id,
        is_played_in_full=excluded.is_played_in_full,exit_status=excluded.exit_status,ended_at=excluded.ended_at,
        events_count=excluded.events_count,completion_reason=excluded.completion_reason,
        client_success=excluded.client_success,projection_version=2,updated_at=now()''',
        (row['id'], row['user_id'], row['song_id'], f"challenge:{row['song_id']}", events[0].occurred_at, row['user_id'],
         str(terminal.elapsed_playing_sec), str(terminal.song_time_sec), row['id'], terminal.event_type == 'finished',
         exit_status, events[0].occurred_at, terminal.occurred_at, len(events), terminal.event_type, client_success))


def rebuild_projections(c, attempt_id):
    """Operator command: caller owns transaction; serialize with finalize/Worker."""
    c.execute('select pg_advisory_xact_lock(hashtext(%s))', (str(attempt_id),))
    row = c.execute('select * from public.piano_attempts where id=%s for update', (attempt_id,)).fetchone()
    if not row or row['status'] == 'CREATED' or not row.get('finalization'):
        raise ValueError('Only finalized v2 attempts can rebuild projections')
    body = Finalize.model_validate(row['finalization'])
    validate_finalization(row, body, row['user_id'])
    project_challenge(c, row, body)
