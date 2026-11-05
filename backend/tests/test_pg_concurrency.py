"""Real PostgreSQL lock regression; never reads DATABASE_URL or a production DB.

CI provides PIANOKT_TEST_POSTGRES_DSN for an isolated test server. Each module
creates and drops its own randomly named database on that server.
"""
import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4
import psycopg
from psycopg import sql
from psycopg.rows import dict_row
import pytest
from fastapi import HTTPException
from test_activity import U, A, packet, full_packet, new_upload
from pianokt_backend.activity import save_activity
from pianokt_backend.upload_service import commit_finalization


@pytest.fixture
def postgres(monkeypatch):
    dsn=os.getenv('PIANOKT_TEST_POSTGRES_DSN')
    if not dsn: pytest.skip('Requires a dedicated PostgreSQL test server')
    monkeypatch.setenv('PIANOKT_ENV','production')
    name='pianokt_test_'+uuid4().hex
    with psycopg.connect(dsn,autocommit=True) as admin:
        for role in ('anon','authenticated','pianokt_backend'):
            if not admin.execute('select 1 from pg_roles where rolname=%s',(role,)).fetchone():
                admin.execute(sql.SQL('create role {}').format(sql.Identifier(role)))
        admin.execute(sql.SQL('create database {}').format(sql.Identifier(name)))
    target=psycopg.conninfo.make_conninfo(dsn,dbname=name)
    try:
        with psycopg.connect(target,autocommit=True) as c:
            c.execute('''create schema auth;
                create table auth.users(id uuid primary key,created_at timestamptz default now());
                create function auth.uid() returns uuid language sql stable as $$ select nullif(current_setting('request.jwt.claim.sub',true),'')::uuid $$;
                create table public.profiles(id uuid primary key,display_name text,avatar_url text);
                create table public.challenge_recordings(id uuid primary key,user_id uuid,song_id text,song_source text,song_title text,duration_sec numeric,midi_storage_path text,midi_keyboard_used boolean,accuracy_pct numeric default 0,difficulty numeric default 0,created_at timestamptz default now());''')
            for name_sql in ('006_play_events_pipeline.sql','007_user_play_logs_song_time_and_recording_link.sql',
                             '009_lakehouse_backend.sql','011_performance_event_telemetry.sql',
                             '013_visits_and_attempt_activity.sql','014_atomic_attempt_commit.sql',
                             '015_direct_export_commit.sql','016_challenge_event_authority.sql','017_async_finalization.sql'):
                c.execute(Path('supabase/migrations',name_sql).read_text())
            c.execute("insert into auth.users(id,created_at) values(%s,'2025-01-01')",(U,))
        yield target
    finally:
        with psycopg.connect(dsn,autocommit=True) as admin:
            admin.execute(sql.SQL('drop database {} with (force)').format(sql.Identifier(name)))


@pytest.mark.parametrize('first,late', [('activity','duplicate'),('finalize','duplicate'),('finalize','interrupted')])
def test_activity_finalize_share_one_transaction_lock(postgres,first,late):
    with psycopg.connect(postgres,row_factory=dict_row) as setup:
        row=new_upload(setup)
    body=full_packet()
    second=psycopg.connect(postgres,row_factory=dict_row)
    pid=second.info.backend_pid
    def contender():
        with second:
            if first=='activity': commit_finalization(second,row,U,body)
            else: save_activity(second,U,body.activity if late=='duplicate' else packet(3,event_sequence=2,event_type='interrupted'))
    with ThreadPoolExecutor(max_workers=1) as pool:
        with psycopg.connect(postgres,row_factory=dict_row) as primary:
            if first=='activity': save_activity(primary,U,packet())
            else: commit_finalization(primary,row,U,body)
            future=pool.submit(contender)
            # Prove the competing writer is waiting on a DB lock, not merely
            # delayed by thread scheduling; transaction release unblocks it.
            with psycopg.connect(postgres,autocommit=True) as observer:
                deadline=time.monotonic()+5
                while time.monotonic()<deadline:
                    state=observer.execute('select wait_event_type from pg_stat_activity where pid=%s',(pid,)).fetchone()
                    if state and state[0]=='Lock': break
                    time.sleep(.01)
                else: pytest.fail('Concurrent writer did not wait for the attempt lock')
        if late=='interrupted':
            with pytest.raises(HTTPException,match='immutable'): future.result(timeout=5)
        else: future.result(timeout=5)
    with psycopg.connect(postgres,row_factory=dict_row) as c:
        assert c.execute('select count(*) n from student_attempt_events').fetchone()['n']==2
        assert c.execute('select count(*) n from piano_outbox').fetchone()['n']==1
        assert c.execute('select count(*) n from play_events_raw').fetchone()['n']==0
