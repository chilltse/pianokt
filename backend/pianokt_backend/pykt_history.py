"""Scoped READY snapshots and explicit, journaled development history synchronization."""
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.parse import quote
from uuid import UUID

from .pipeline.direct import CommittedResults
from .pipeline.selection import attempt_order_key, rejection, utc
from .pipeline.attempts import json_value
from .song_identity import assert_export_identity
from .pykt_integration import CompatibilityError, KC_FEATURE_NAMES, MODEL_FIELDS, canonical, validate_vector
from .pykt_recommendation import fingerprint


def development(environment):
    if environment not in ('development', 'test'):
        raise CompatibilityError('PyKT recommendation/sync requires explicit development or test environment')
    return environment


def student_identity(environment, learner_key):
    development(environment)
    return f'pianokt:{environment}:{UUID(str(learner_key))}'


def read_snapshot(connection, objects, uid, environment):
    """Authenticated user scope in every query; caller supplies repeatable-read transaction."""
    development(environment)
    learner = connection.execute('select learner_key from public.piano_learner_keys where user_id=%s', (uid,)).fetchone()
    if not learner:
        raise CompatibilityError('Missing learner_key; no implicit learner creation')
    key = str(learner['learner_key'])
    rows = connection.execute('''select p.id as attempt_id,p.learner_key,p.alignment_run_id,p.result_key,
        p.reference_hash,p.performance_hash,p.result_digest,p.song_id,p.segment_id,p.segment_index,p.midi_sha256,
        p.result_committed_at,p.summary,p.source_environment,p.metadata
        from public.piano_attempts p where p.user_id=%s and p.learner_key=%s
        and p.source_environment=%s and p.status='READY' order by p.id''', (uid, key, environment)).fetchall()
    context = connection.execute('''select a.*,k.learner_key,l.time_playing,l.is_played_in_full
        from public.student_attempts a join public.piano_learner_keys k on k.user_id=a.user_id
        join public.student_visits v on v.id=a.visit_id and v.user_id=a.user_id
        left join public.user_play_logs l on l.session_id=a.attempt_id and l.user_id=a.user_id
        where a.user_id=%s and k.learner_key=%s and a.source_environment=%s and v.source_environment=%s''',
        (uid, key, environment, environment)).fetchall()
    return build_snapshot(rows, context, objects, key, environment)


def build_snapshot(rows, contexts, objects, learner_key, environment):
    development(environment)
    by_id = {str(a['attempt_id']): a for a in contexts}
    if len(by_id) != len(contexts) or len({str(r['attempt_id']) for r in rows}) != len(rows):
        raise CompatibilityError('Duplicate history context/commit')
    if any(str(r['learner_key']) != learner_key or r['source_environment'] != environment for r in rows):
        raise CompatibilityError('History user/environment mismatch')
    cutoff = datetime.now(timezone.utc)
    records = list(CommittedResults(rows).results(objects, cutoff, environment=environment))
    if len(records) != len(rows):
        raise CompatibilityError('READY commits outside current snapshot')
    ordered, sources, skipped = [], [], []
    for r in records:
        a = by_id.get(r['attempt_id'])
        if (not a or str(a['learner_key']) != learner_key or a.get('source_environment') != environment
                or not a.get('visit_id') or type(a.get('visit_attempt_sequence')) is not int
                or not a.get('ended_at') or utc(a['ended_at']) >= cutoff
                or a.get('time_playing') is None or type(a.get('is_played_in_full')) is not bool):
            raise CompatibilityError('Missing or mismatched finalized history context')
        assert_export_identity(r, a)
        if r.get('kc_labeler_version') != '1.2.0':
            raise CompatibilityError('Mixed/unsupported history labeler')
        data = r['events_data']
        if data.get('kc_feature_names') != KC_FEATURE_NAMES:
            raise CompatibilityError('History field names/order mismatch')
        for t, vector, reject in zip(data['onset_seconds'], data['kc_vectors'], data['reject_reason']['event'], strict=True):
            import math
            if type(t) not in (int, float) or not math.isfinite(t) or t < 0 or type(reject) is not int or reject not in (0, 1):
                raise CompatibilityError('Invalid history onset/response')
            validate_vector(vector)
        order = attempt_order_key(a)
        reason = rejection(r)
        if r.get('_attempt_settings', {}).get('waiting') or (a.get('settings') or {}).get('waiting'):
            reason = 'waiting'
        source = dict(attempt_id=r['attempt_id'], result_digest=r['_committed_digest'],
                      alignment_run_id=r['alignment_run_id'], order=json_value(order), skip_reason=reason)
        sources.append(source)
        if reason:
            skipped.append(source)
        else:
            events = [dict(event_id=f"{r['attempt_id']}:onset:{i}", kc_vector=v, response=1-e)
                      for i, (v, e) in enumerate(zip(data['kc_vectors'], data['reject_reason']['event'], strict=True))]
            ordered.append((order, dict(attempt_id=r['attempt_id'], events=events)))
    ordered.sort(key=lambda p: p[0])
    sources.sort(key=lambda p: p['attempt_id'])
    attempts = [p[1] for p in ordered]
    snapshot = dict(student_id=student_identity(environment, learner_key), learner_key=learner_key,
                    environment=environment, sources=sources, attempts=attempts, skipped=skipped,
                    event_count=sum(len(a['events']) for a in attempts), kc_labeler_version='1.2.0')
    snapshot['snapshot_digest'] = fingerprint(snapshot)
    return snapshot


def binding_path(root, student):
    return Path(root) / (fingerprint(student) + '.json')


@contextmanager
def journal_lock(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix('.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def save(path, value):
    with NamedTemporaryFile(mode='w', dir=path.parent, delete=False) as file:
        json.dump(value, file, sort_keys=True, allow_nan=False)
        file.flush(); os.fsync(file.fileno())
        name = file.name
    os.replace(name, path)
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def contract_binding(client):
    from dataclasses import asdict
    return dict(contract=asdict(client.compatibility), endpoint=str(client.http.base_url))


def verify_binding(binding, snapshot, state, client):
    expected = dict(student_id=snapshot['student_id'], snapshot_digest=snapshot['snapshot_digest'],
                    event_count=snapshot['event_count'], **contract_binding(client))
    if (binding.get('status') != 'complete' or any(binding.get(k) != v for k, v in expected.items())
            or state['student_id'] != snapshot['student_id']
            or state['history_revision'] != snapshot['event_count'] or state['next_position'] != snapshot['event_count']
            or binding.get('final_state') != state):
        raise CompatibilityError('History sync binding missing/stale; run development sync CLI explicitly')
    return {k: binding[k] for k in ('student_id', 'snapshot_digest', 'event_count', 'contract', 'endpoint', 'final_state', 'status')}


def sync_history(client, snapshot, root, *, apply=False):
    development(snapshot['environment'])
    client.inspect()
    student = snapshot['student_id']
    if student != student_identity(snapshot['environment'], snapshot['learner_key']):
        raise CompatibilityError('Student namespace mismatch')
    path = binding_path(root, student)
    state = client._state(student)
    if not apply:
        return dict(dry_run=True, student_id=student, snapshot_digest=snapshot['snapshot_digest'],
                    event_count=snapshot['event_count'], state=state, skipped=snapshot['skipped'])
    with journal_lock(path):
        journal = json.loads(path.read_text()) if path.exists() else None
        scope = contract_binding(client)
        all_events = [e for a in snapshot['attempts'] for e in a['events']]
        if journal:
            if journal.get('student_id') != student or any(journal.get(k) != v for k, v in scope.items()):
                raise CompatibilityError('Existing journal identity/model/endpoint conflict')
            old_events = [e for batch in journal['batches'] for e in batch['events']]
            if canonical(all_events[:len(old_events)]) != canonical(old_events):
                raise CompatibilityError('History correction/reordering detected; no rewrite allowed')
            # Source identity must remain exact for previous eligible attempts, even if vectors coincidentally match.
            current = {s['attempt_id']: s for s in snapshot['sources']}
            if any(current.get(s['attempt_id']) != s for s in journal['sources'] if s['skip_reason'] is None):
                raise CompatibilityError('Previously synced source commit changed')
            offset = len(old_events)
        else:
            if state['history_revision'] != 0 or state['next_position'] != 0:
                raise CompatibilityError('Nonempty remote history without provenance; refusing adoption')
            journal = dict(student_id=student, batches=[], **scope)
            offset = 0
        limit = min(client.inspect()['limits']['max_batch_events'], 4096)
        if type(limit) is not int or limit < 1:
            raise CompatibilityError('Invalid max_batch_events')
        # Persist deterministic chunk identity before any write. Retries reuse stored chunks.
        for start in range(offset, len(all_events), limit):
            events = all_events[start:start+limit]
            journal['batches'].append(dict(attempt_id='sync-' + fingerprint(events), events=events,
                                            start=start, done=False))
        journal.update(status='syncing', sources=snapshot['sources'], snapshot_digest=snapshot['snapshot_digest'],
                       event_count=len(all_events))
        save(path, journal)
        for batch in journal['batches']:
            if batch['done']:
                continue
            begin, end = batch['start'], batch['start'] + len(batch['events'])
            state = client._state(student)
            if state['history_revision'] not in (begin, end) or state['next_position'] != state['history_revision']:
                raise CompatibilityError('Remote history conflict; refusing overwrite')
            if len(batch['events']) > limit:
                raise CompatibilityError('Deployment batch limit changed; cannot rewrite pending chunk')
            result = client._request('POST', f'/kt/students/{quote(student, safe="")}/observe-batch', json=dict(
                kc_feature_names=list(MODEL_FIELDS), attempt_id=batch['attempt_id'], events=batch['events'], expected_revision=begin))
            observations = result.get('observations', [])
            if (result.get('atomic') is not True or result.get('state') != client._state(student)
                    or result['state']['history_revision'] != end or result['state']['next_position'] != end
                    or [o['event_id'] for o in observations] != [e['event_id'] for e in batch['events']]
                    or any(o.get('duplicate') is not (state['history_revision'] == end) for o in observations)):
                raise CompatibilityError('Unexpected observe acknowledgement; journal remains incomplete')
            batch['done'] = True
            save(path, journal)
        final = client._state(student)
        if final['history_revision'] != len(all_events) or final['next_position'] != len(all_events):
            raise CompatibilityError('Remote history revision does not match complete source')
        journal.update(status='complete', final_state=final)
        save(path, journal)
        return verify_binding(journal, snapshot, final, client)
