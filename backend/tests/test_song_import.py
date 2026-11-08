"""MIDI validation, content-addressed publication and CLI tests (no cloud credentials)."""
import copy
import io
import json
import uuid
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError

import mido
import pytest
from pianokt_backend import song_import as module

ROOT = Path(__file__).resolve().parents[2]


def make_midi(tmp_path, pitch=60):
    midi = mido.MidiFile()
    track = mido.MidiTrack()
    midi.tracks.append(track)
    track.extend([mido.MetaMessage('set_tempo',tempo=600000),
                  mido.MetaMessage('key_signature',key='Am'),
                  mido.MetaMessage('time_signature',numerator=3,denominator=4),
                  mido.Message('note_on',note=pitch,velocity=80),
                  mido.Message('note_off',note=pitch,time=480)])
    path = tmp_path/f'{pitch}.mid'
    midi.save(path)
    return path


def test_analysis_extracts_original_bytes_and_maps(tmp_path):
    path = make_midi(tmp_path)
    item = module.analyze_midi(path)
    assert item['raw'] == path.read_bytes()
    assert item['duration_ms'] == 600 and item['note_count'] == 1 and item['track_count'] == 1
    assert item['midi_metadata']['tempos'] == [{'time_ms':0,'bpm':100.0}]
    assert item['midi_metadata']['key_signatures'] == [{'time_ms':0,'key':'Am'}]
    assert item['midi_metadata']['time_signatures'][0]['numerator'] == 3
    assert item['storage_path'] == f"midi/{item['midi_sha256']}.mid"
    assert item['midi_sha256'] == module.analyze_midi(path)['midi_sha256']


@pytest.mark.parametrize('raw', [b'bad', b'MThd'+b'\0'*40, b'MThd'+b'\0'*module.MAX_BYTES])
def test_reject_invalid_midi(tmp_path, raw):
    path=tmp_path/'invalid.mid'; path.write_bytes(raw)
    with pytest.raises(ValueError): module.analyze_midi(path)


def test_unterminated_notes_rejected(tmp_path):
    path=make_midi(tmp_path)
    midi=mido.MidiFile(path)
    midi.tracks[0]=mido.MidiTrack(msg for msg in midi.tracks[0] if msg.type!='note_off')
    midi.save(path)
    with pytest.raises(ValueError,match='without note-off'): module.analyze_midi(path)


def test_all_archived_samples_are_parseable():
    paths=list((ROOT/'scripts/song_catalog/midi').glob('*.mid'))
    assert len(paths)==17
    for path in paths:
        assert module.analyze_midi(path)['note_count']>0


@pytest.mark.parametrize('changes', [dict(license=''),dict(source_url='[https://example.com](https://example.com)'),dict(segment_index=0),dict(difficulty=float('nan'))])
def test_metadata_requires_explicit_license_url_and_valid_segment(changes):
    args=dict(title='Song',composer='Composer',license='verified-license',source_url='https://example.com/source',slug='song',difficulty=.5,segment_index=1)
    args.update(changes)
    with pytest.raises(ValueError): module.validate_metadata(**args)


class FakeStorage(module.MidiStorage):
    def __init__(self):
        super().__init__('https://storage.example','server-only')
        self.objects={}; self.uploads=0; self.corrupt_public=False
    def request(self,path,raw=None,public=False):
        if raw is not None:
            assert path not in self.objects
            self.objects[path]=raw; self.uploads+=1
        if path not in self.objects:
            raise HTTPError('storage',404,'Not Found',{},io.BytesIO())
        return b'corrupt' if public and self.corrupt_public else self.objects[path]


def test_storage_hash_dedup_and_public_verification(tmp_path):
    item=module.analyze_midi(make_midi(tmp_path)); storage=FakeStorage()
    assert storage.ensure(item)
    assert not storage.ensure(item)
    assert storage.uploads==1
    storage.corrupt_public=True
    with pytest.raises(ValueError,match='SHA-256 mismatch'): storage.ensure(item)


class FakeDB:
    def __init__(self): self.songs={}; self.segments={}; self.contents={}
    @contextmanager
    def transaction(self):
        snapshot=copy.deepcopy((self.songs,self.segments,self.contents))
        try: yield
        except Exception:
            self.songs,self.segments,self.contents=snapshot
            raise
    def execute(self,sql,params=()):
        query=' '.join(sql.split()); row=None
        if query.startswith('select pg_advisory'): pass
        elif query.startswith('select * from public.piano_song_catalog'):
            row=next((song for song in self.songs.values() if song['song_id']==params[0]),None) if 'song_id=%s' in query else next((song for song in self.songs.values() if song['slug']==params[0]),None)
        elif query.startswith('insert into public.piano_song_catalog'):
            row=dict(zip(('song_id','slug','title','composer','license','source_url','difficulty','genres'),params));self.songs[row['song_id']]=row
        elif query.startswith('select * from public.piano_song_segments'):
            row=next((segment for segment in self.segments.values() if segment['song_id']==params[0] and segment['segment_index']==params[1]),None)
        elif query.startswith('insert into public.piano_song_segments'):
            row=dict(zip(('segment_id','song_id','segment_index','title'),params));row['active_midi_sha256']=None;self.segments[row['segment_id']]=row
        elif query.startswith('select midi_sha256 from public.piano_song_midis'):
            row=self.contents.get((params[0],params[1]))
        elif query.startswith('insert into public.piano_song_midis'):
            row=dict(zip(('song_id','segment_id','midi_sha256','storage_path','duration_ms','note_count','track_count','midi_metadata','license','source_url'),params));self.contents[row['segment_id'],row['midi_sha256']]=row
        elif query.startswith('update public.piano_song_segments'):
            self.segments[params[1]].update(active_midi_sha256=params[0],status='published')
        elif query.startswith('update public.piano_song_catalog'):
            self.songs[params[0]]['status']='published'
        else: raise AssertionError(query)
        return SimpleNamespace(fetchone=lambda:row)


def publication_args():
    return dict(title='Nocturne',composer='Composer',license='actually-verified',source_url='https://example.com/source',slug='composer-nocturne')


def test_publication_is_idempotent_and_segments_share_parent_not_identity(tmp_path):
    db=FakeDB(); item=module.analyze_midi(make_midi(tmp_path)); args=publication_args()
    one=module.publish(db,item,**args)
    repeat=module.publish(db,item,**args)
    assert repeat['song_id']==one['song_id'] and repeat['segment_id']==one['segment_id']
    assert not repeat['content_created']
    two=module.publish(db,item,**args,song_id=uuid.UUID(one['song_id']),segment_index=2)
    assert two['song_id']==one['song_id'] and two['segment_id']!=one['segment_id'] and two['segment_index']==2
    assert len(db.songs)==1 and len(db.segments)==2 and len(db.contents)==2
    assert all('song_version_id' not in record for record in (one,two))


def test_new_content_activates_without_overwrite_and_old_replay_fails(tmp_path):
    db=FakeDB(); args=publication_args()
    old=module.analyze_midi(make_midi(tmp_path)); new=module.analyze_midi(make_midi(tmp_path,62))
    one=module.publish(db,old,**args)
    two=module.publish(db,new,**args)
    assert one['segment_id']==two['segment_id'] and one['midi_sha256']!=two['midi_sha256']
    assert len(db.contents)==2
    with pytest.raises(ValueError,match='superseded'): module.publish(db,old,**args)
    assert db.segments[uuid.UUID(one['segment_id'])]['active_midi_sha256']==new['midi_sha256']
    with pytest.raises(ValueError,match='license differs'):
        module.publish(db,new,**{**args,'license':'invented'})
    assert len(db.songs)==1


def test_cli_dry_run_requires_no_credentials(tmp_path, monkeypatch, capsys):
    import sys
    from pianokt_backend import cli
    for name in ('SUPABASE_URL','SUPABASE_SERVICE_ROLE_KEY','DATABASE_URL'): monkeypatch.delenv(name,raising=False)
    monkeypatch.setattr(sys,'argv',['pianokt-data','import-song',str(make_midi(tmp_path)),
        '--title','Nocturne','--composer','Chopin','--license','verified-license',
        '--source-url','https://example.com/source','--segment-index','2','--dry-run'])
    cli.main()
    output=json.loads(capsys.readouterr().out)
    assert output['status']=='validated' and output['segment_index']==2
    assert output['license']=='verified-license' and 'song_version_id' not in output
