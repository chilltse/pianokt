"""Published Supabase piano-midi candidates; never download catalog MIDI from RAW_ROOT."""
from collections import Counter
from hashlib import sha256
from pathlib import Path
import re
from tempfile import TemporaryDirectory
from uuid import UUID

import httpx

from .pykt_integration import ReferenceCandidates
from .song_import import MAX_BYTES

CATALOG_SQL = '''select song_id::text, segment_id::text, segment_index, title,
    segment_title, midi_sha256, storage_path, midi_metadata
    from public.piano_public_song_catalog order by song_id,segment_index,segment_id'''


class CatalogCandidateProvider:
    def __init__(self, http, supabase_url, *, context_provenance=None):
        self.http = http
        self.url = supabase_url.rstrip('/')
        # Administrator-audited, hash-bound sidecar; no table/schema mutation needed.
        self.provenance = context_provenance or {}
        self.extractor = ReferenceCandidates()
        self.bytes_cache = {}

    def load(self, catalog):
        output = []
        identities = Counter((r.get('song_id'), r.get('segment_id')) for r in catalog)
        for record in catalog:
            item = {k: record.get(k) for k in ('song_id', 'segment_id', 'segment_index', 'title', 'midi_sha256')}
            item.update(status='skipped', onset_count=0, unknown_question_ratio=None,
                        unknown_field_ratio=None, rows=[])
            try:
                identity = (str(UUID(record['song_id'])), str(UUID(record['segment_id'])))
                if identities[(record['song_id'], record['segment_id'])] != 1:
                    raise ValueError('duplicate_catalog_identity')
                if type(record['segment_index']) is not int or record['segment_index'] < 1:
                    raise ValueError('invalid_segment_index')
                h = record['midi_sha256']
                if not isinstance(h, str) or not re.fullmatch('[a-f0-9]{64}', h) or record['storage_path'] != f'midi/{h}.mid':
                    raise ValueError('invalid_storage_path_or_hash')
                proof = self.provenance.get(record['segment_id'], {})
                if (proof.get('midi_sha256') != h or proof.get('kc_labeler_version') != '1.2.0'
                        or proof.get('context') != 'standalone_reference' or not proof.get('evidence')):
                    raise ValueError('missing_standalone_context_provenance')
                if (record.get('midi_metadata') or {}).get('requires_full_context'):
                    raise ValueError('full_reference_context_required')
                if h not in self.bytes_cache:
                    with self.http.stream('GET', f'{self.url}/storage/v1/object/public/piano-midi/midi/{h}.mid',
                                          timeout=15, follow_redirects=False) as response:
                        response.raise_for_status()
                        raw = bytearray()
                        for chunk in response.iter_bytes():
                            raw.extend(chunk)
                            if len(raw) > MAX_BYTES:
                                raise ValueError('midi_size_limit')
                    if sha256(raw).hexdigest() != h:
                        raise ValueError('midi_sha256_mismatch')
                    self.bytes_cache[h] = bytes(raw)
                with TemporaryDirectory(prefix='pykt-reference-') as tmp:
                    path = Path(tmp) / 'reference.mid'
                    path.write_bytes(self.bytes_cache[h])
                    rows = self.extractor.extract(path, song_id=record['song_id'], song_version=h,
                                                  segment_id=record['segment_id'])
                if not rows:
                    raise ValueError('empty_reference')
                item.update(status='eligible', onset_count=len(rows), rows=rows, song_version=h)
            except httpx.HTTPError:
                item['reason'] = 'storage_unavailable'  # Never emit credential-bearing URLs.
            except (ValueError, KeyError, TypeError, OSError, EOFError) as exc:
                # Stable public codes only; parser exceptions may contain local paths.
                error = str(exc)
                allowed = {'duplicate_catalog_identity', 'invalid_segment_index', 'invalid_storage_path_or_hash',
                           'missing_standalone_context_provenance', 'full_reference_context_required',
                           'midi_size_limit', 'midi_sha256_mismatch', 'empty_reference'}
                item['reason'] = error if error in allowed else 'invalid_reference'
            output.append(item)
        return output
