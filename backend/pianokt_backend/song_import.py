"""Administrator-only, content-addressed song/segment importer.

Storage objects and MIDI content records are immutable. Publication happens only after an
anonymous CDN download has the expected digest. Failed transactions may leave an
unreferenced object; retry verifies/reuses it instead of deleting shared content.
"""
import hashlib
import io
import json
import math
import os
import re
import unicodedata
import urllib.error
import urllib.request
import uuid
from collections import defaultdict, deque
from pathlib import Path
from urllib.parse import urlparse

MAX_BYTES = 10 * 1024 * 1024
MAX_NOTES = 500_000
MAX_DURATION_SECONDS = 14_400


def analyze_midi(path: Path):
    import mido
    with path.open('rb') as stream:
        raw = stream.read(MAX_BYTES + 1)
    if not 14 <= len(raw) <= MAX_BYTES or raw[:4] != b'MThd':
        raise ValueError('MIDI must have an MThd header and be at most 10 MiB')
    try:
        midi = mido.MidiFile(file=io.BytesIO(raw), clip=False)
        if midi.type not in (0, 1) or not midi.tracks or midi.ticks_per_beat <= 0:
            raise ValueError('Only type 0/1 MIDI with PPQ timing is supported')
        if len(midi.tracks) > 256:
            raise ValueError('MIDI has more than 256 tracks')
        note_count = 0
        musical_tracks = 0
        zero_duration_notes = 0
        unmatched_note_offs = 0
        for track in midi.tracks:
            active = defaultdict(deque)
            tick = 0
            track_notes = 0
            for msg in track:
                if not isinstance(msg.time, int) or msg.time < 0:
                    raise ValueError('Invalid MIDI delta time')
                tick += msg.time
                if msg.type == 'note_on' and msg.velocity > 0:
                    active[(msg.channel, msg.note)].append(tick)
                    note_count += 1
                    track_notes += 1
                    if note_count > MAX_NOTES:
                        raise ValueError('MIDI exceeds 500,000 notes')
                elif msg.type == 'note_off' or (msg.type == 'note_on' and msg.velocity == 0):
                    pending = active[(msg.channel, msg.note)]
                    if not pending:
                        unmatched_note_offs += 1
                    elif tick == pending.popleft():
                        zero_duration_notes += 1
            if any(active.values()):
                raise ValueError('MIDI contains notes without note-off')
            musical_tracks += int(track_notes > 0)
        if note_count <= zero_duration_notes:
            raise ValueError('MIDI contains no playable notes')
        seconds = 0.0
        tempo = 500_000
        tempos = []
        keys = []
        signatures = []
        for msg in mido.merge_tracks(midi.tracks):
            seconds += mido.tick2second(msg.time, midi.ticks_per_beat, tempo)
            if seconds > MAX_DURATION_SECONDS:
                raise ValueError('MIDI duration exceeds four hours')
            if msg.type == 'set_tempo':
                if msg.tempo <= 0:
                    raise ValueError('Invalid MIDI tempo')
                tempo = msg.tempo
                tempos.append(dict(time_ms=round(seconds * 1000), bpm=round(mido.tempo2bpm(tempo), 6)))
            elif msg.type == 'key_signature':
                keys.append(dict(time_ms=round(seconds * 1000), key=msg.key))
            elif msg.type == 'time_signature':
                signatures.append(dict(time_ms=round(seconds * 1000), numerator=msg.numerator, denominator=msg.denominator))
        if not math.isfinite(seconds) or seconds <= 0:
            raise ValueError('Invalid MIDI duration')
        if not tempos or tempos[0]['time_ms'] > 0:
            tempos.insert(0, dict(time_ms=0, bpm=120.0))
        # Missing key/time signatures remain unknown, rather than inferred.
        metadata = dict(format=midi.type, ticks_per_beat=midi.ticks_per_beat,
                        musical_track_count=musical_tracks, tempos=tempos,
                        key_signatures=keys, time_signatures=signatures,
                        validation_warnings=dict(zero_duration_notes=zero_duration_notes, unmatched_note_offs=unmatched_note_offs))
    except (EOFError, OSError, KeyError, TypeError, OverflowError) as exc:
        raise ValueError('Malformed MIDI file') from exc
    digest = hashlib.sha256(raw).hexdigest()
    return dict(raw=raw, midi_sha256=digest, storage_path=f'midi/{digest}.mid',
                duration_ms=max(1, round(seconds * 1000)), note_count=note_count,
                track_count=len(midi.tracks), midi_metadata=metadata)


def slugify(title, composer):
    value = unicodedata.normalize('NFKD', f'{composer}-{title}')
    value = ''.join(char for char in value if not unicodedata.combining(char))
    return re.sub(r'[^\w]+', '-', value.lower(), flags=re.UNICODE).strip('-')[:200]


def validate_metadata(title, composer, license, source_url, slug, difficulty, segment_index):
    for name, value, maximum in [('title', title, 300), ('composer', composer, 300), ('license', license, 1000), ('slug', slug, 200)]:
        if not value or not value.strip() or len(value) > maximum:
            raise ValueError(f'{name} is required and must be at most {maximum} characters')
    url = urlparse(source_url)
    if url.scheme not in ('http', 'https') or not url.netloc or url.username or url.password:
        raise ValueError('source-url must be an HTTP(S) URL without credentials (not a Markdown link)')
    if not math.isfinite(difficulty) or not 0 <= difficulty <= 1:
        raise ValueError('difficulty must be between 0 and 1')
    if segment_index < 1:
        raise ValueError('segment-index must be at least 1')


class MidiStorage:
    def __init__(self, base, key):
        if urlparse(base).scheme != 'https' and urlparse(base).hostname not in ('localhost', '127.0.0.1', '::1'):
            raise ValueError('SUPABASE_URL must use HTTPS (except local development)')
        self.base = base.rstrip('/')
        self.key = key

    def request(self, path, raw=None, public=False):
        headers = {} if public else {'Authorization': f'Bearer {self.key}', 'apikey': self.key}
        if raw is not None:
            headers.update({'Content-Type': 'audio/midi', 'Cache-Control': 'public,max-age=31536000,immutable', 'x-upsert': 'false'})
        prefix = 'public/' if public else ''
        request = urllib.request.Request(f'{self.base}/storage/v1/object/{prefix}piano-midi/{path}',
                                         data=raw, headers=headers, method='POST' if raw is not None else 'GET')
        with urllib.request.urlopen(request, timeout=120) as response:
            return response.read(MAX_BYTES + 1)

    @staticmethod
    def missing(exc):
        # Supabase Storage uses HTTP 400 with a JSON 404 statusCode for absent keys.
        if exc.code == 404:
            return True
        if exc.code != 400:
            return False
        try:
            body = json.loads(exc.read())
            return str(body.get('statusCode')) == '404'
        except (ValueError, AttributeError):
            return False

    def ensure(self, item):
        path = item['storage_path']
        uploaded = False
        try:
            stored = self.request(path)
        except urllib.error.HTTPError as exc:
            if not self.missing(exc):
                raise
            try:
                self.request(path, item['raw'])
                uploaded = True
            except urllib.error.HTTPError as conflict:
                # Concurrent content upload may win. Verify its bytes, never overwrite.
                if conflict.code not in (400, 409):
                    raise
            stored = self.request(path)
        for content in (stored, self.request(path, public=True)):
            if hashlib.sha256(content).hexdigest() != item['midi_sha256']:
                raise ValueError('Storage/CDN SHA-256 mismatch; song was not published')
        return uploaded


def publish(c, item, *, title, composer, license, source_url, slug, song_id=None,
            segment_index=1, segment_title=None, difficulty=.5, genres=()):
    """Called only after Storage.ensure; serializable logical identity under locks."""
    from psycopg.types.json import Jsonb
    with c.transaction():
        # Slug protects retries/first insertion; UUID protects all segments of a song.
        c.execute('select pg_advisory_xact_lock(hashtextextended(%s,0))', ('song-slug:'+slug,))
        if song_id:
            song = c.execute('select * from public.piano_song_catalog where song_id=%s for update', (song_id,)).fetchone()
            if not song:
                raise ValueError('Unknown song-id; omit it when creating a new song')
        else:
            song = c.execute('select * from public.piano_song_catalog where slug=%s for update', (slug,)).fetchone()
        if song:
            sid = song['song_id']
            for key, expected in [('title',title), ('composer',composer), ('license',license), ('source_url',source_url)]:
                if song[key] != expected:
                    raise ValueError(f'Existing song {key} differs; use a new slug for a different arrangement')
            # Importing MIDI should not silently edit catalog metadata.
        else:
            sid = uuid.uuid4()
            c.execute('''insert into public.piano_song_catalog(song_id,slug,title,composer,license,source_url,difficulty,genres)
                values(%s,%s,%s,%s,%s,%s,%s,%s)''', (sid,slug,title,composer,license,source_url,difficulty,list(genres)))
        segment = c.execute('select * from public.piano_song_segments where song_id=%s and segment_index=%s for update', (sid,segment_index)).fetchone()
        if segment:
            segment_id = segment['segment_id']
            if segment_title is not None and segment['title'] != segment_title:
                raise ValueError('Existing segment title differs; import does not rename segments')
        else:
            segment_id = uuid.uuid4()
            c.execute('insert into public.piano_song_segments(segment_id,song_id,segment_index,title) values(%s,%s,%s,%s)',
                      (segment_id,sid,segment_index,segment_title))
        content = c.execute('select midi_sha256 from public.piano_song_midis where segment_id=%s and midi_sha256=%s', (segment_id,item['midi_sha256'])).fetchone()
        created = not bool(content)
        if content:
            if segment and segment['active_midi_sha256'] not in (None, item['midi_sha256']):
                raise ValueError('This MIDI was superseded; active MIDI was not changed')
        else:
            c.execute("""insert into public.piano_song_midis(song_id,segment_id,midi_sha256,storage_path,duration_ms,note_count,track_count,midi_metadata,license,source_url)
                values(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (sid,segment_id,item['midi_sha256'],item['storage_path'],item['duration_ms'],item['note_count'],item['track_count'],Jsonb(item['midi_metadata']),license,source_url))
        c.execute("update public.piano_song_segments set active_midi_sha256=%s,status='published' where segment_id=%s", (item['midi_sha256'],segment_id))
        c.execute("update public.piano_song_catalog set status='published' where song_id=%s", (sid,))
    return dict(song_id=str(sid),segment_id=str(segment_id),segment_index=segment_index,
                midi_sha256=item['midi_sha256'],content_created=created,status='published',slug=song['slug'] if song else slug)


def add_import_parser(sub):
    p = sub.add_parser('import-song', help='Validate, upload and publish an official MIDI song/segment')
    p.add_argument('midi', type=Path)
    p.add_argument('--title', required=True)
    p.add_argument('--composer', required=True)
    p.add_argument('--license', required=True, help='Actual verified license; no default')
    p.add_argument('--source-url', required=True)
    p.add_argument('--slug', help='Unique arrangement identifier; defaults to composer-title')
    p.add_argument('--song-id', type=uuid.UUID, help='Existing parent song UUID when adding a segment or updating MIDI content')
    p.add_argument('--segment-index', type=int, default=1, help='Stable position within this song, starting at 1')
    p.add_argument('--segment-title')
    p.add_argument('--difficulty', type=float, default=.5, help='0..1; 0.5 is an uncalibrated default')
    p.add_argument('--genre', action='append', default=[])
    p.add_argument('--dry-run', action='store_true', help='Validate and show metadata without any credentials/network/database changes')


def run_import(args):
    slug = args.slug or slugify(args.title, args.composer)
    validate_metadata(args.title,args.composer,args.license,args.source_url,slug,args.difficulty,args.segment_index)
    if args.segment_title is not None and not 1 <= len(args.segment_title.strip()) <= 300:
        raise ValueError('segment-title must have 1..300 characters')
    item = analyze_midi(args.midi)
    report = {key:value for key,value in item.items() if key != 'raw'}
    report.update(slug=slug,title=args.title,composer=args.composer,license=args.license,
                  source_url=args.source_url,segment_index=args.segment_index,segment_title=args.segment_title)
    if args.dry_run:
        return dict(report,status='validated',dry_run=True)
    import psycopg
    from psycopg.rows import dict_row
    for name in ('SUPABASE_URL','SUPABASE_SERVICE_ROLE_KEY','DATABASE_URL'):
        if not os.environ.get(name):
            raise ValueError(f'Missing administrator environment variable: {name}')
    storage = MidiStorage(os.environ['SUPABASE_URL'], os.environ['SUPABASE_SERVICE_ROLE_KEY'])
    uploaded = storage.ensure(item)
    with psycopg.connect(os.environ['DATABASE_URL'],autocommit=True,row_factory=dict_row,connect_timeout=15) as c:
        result = publish(c,item,title=args.title,composer=args.composer,license=args.license,
                         source_url=args.source_url,slug=slug,song_id=args.song_id,
                         segment_index=args.segment_index,segment_title=args.segment_title,
                         difficulty=args.difficulty,genres=args.genre)
    return {**report, **result, 'storage_uploaded': uploaded}
