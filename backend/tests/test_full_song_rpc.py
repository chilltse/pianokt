"""Use browser roles: admin-only MIDI verification misses invoker permissions."""
from pathlib import Path
import pytest
import re
from test_activity import db


def migration(db, name):
    text = Path('supabase/migrations', name).read_text()
    statement = ''
    for token in re.findall(r"\$\$.*?\$\$|'(?:''|[^'])*'|--[^\n]*|;|[^;'$-]+|-|\$", text, re.S):
        if token == ';':
            if statement.strip(): db.execute(statement)
            statement = ''
        else: statement += token


def test_full_song_rpc_exposes_only_active_published_resources(db):
    migration(db, '023_full_song_catalog.sql')
    for i,status in [(1,'published'),(2,'draft')]:
        sid=f'70000000-0000-0000-0000-{i:012}'
        db.execute("insert into piano_song_catalog(song_id,slug,title,composer,license,source_url,status) values(%s,%s,'Fixture','Composer','test','https://example.invalid',%s)",(sid,f'fixture-{i}',status))
        for h in ('a'*64,'b'*64):
            db.execute("insert into piano_song_full_midis(song_id,midi_sha256,storage_path,duration_ms,note_count,track_count,license,source_url) values(%s,%s,%s,1000,1,1,'test','https://example.invalid')",(sid,h,f'midi/{h}.mid'))
        db.execute('update piano_song_catalog set active_full_midi_sha256=%s where song_id=%s',('a'*64,sid))
    db.execute('set role anon')
    with pytest.raises(RuntimeError,match='permission denied'):
        db.execute('select piano_public_full_song_catalog()')
    db.execute('reset role')
    migration(db, '026_full_song_catalog_rpc_permissions.sql')
    for role in ('anon','authenticated'):
        db.execute('set role '+role)
        result=db.execute('select piano_public_full_song_catalog() as songs').fetchone()['songs']
        assert len(result)==1 and result[0]['song_id']=='70000000-0000-0000-0000-000000000001'
        assert result[0]['midi_sha256']=='a'*64
        with pytest.raises(RuntimeError,match='permission denied'):
            db.execute('select * from piano_song_catalog')
        db.execute('reset role')
