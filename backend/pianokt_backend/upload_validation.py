"""Validate once per Worker run; reuse the exact verified bytes for alignment."""
import json
import mido
from pathlib import Path
from fastapi import HTTPException
from .contracts import digest, check_midi
from .upload_contracts import validate_performance_events

class InvalidUpload(ValueError):
    pass

def read_verified(store, row, kind, maximum, generations):
    key = row[kind + '_key']
    if store.bucket is not None:
        blob = store.bucket.blob(store._name(key))
        pinned = (row.get('file_generations') or {}).get(kind)
        if pinned is not None:
            blob = store.bucket.blob(store._name(key), generation=int(pinned))
        blob.reload(timeout=30)
        if blob.size is None or blob.size > maximum:
            raise InvalidUpload(kind + ': size exceeds limit')
        generations[kind] = str(blob.generation)
        data = blob.download_as_bytes(if_generation_match=blob.generation, timeout=60)
    else:
        path = store.path / store._name(key)
        if path.stat().st_size > maximum:
            raise InvalidUpload(kind + ': size exceeds limit')
        data = path.read_bytes()
    if len(data) > maximum or digest(data) != row[kind + '_hash']:
        raise InvalidUpload(kind + ': content identity mismatch')
    return data

def validate_files(store, row, directory):
    generations = {}
    files = {}
    for kind in ('performance', 'reference'):
        data = read_verified(store, row, kind, 8_000_000, generations)
        path = Path(directory) / (kind + '.mid')
        path.write_bytes(data)
        try:
            check_midi(path)
            mido.MidiFile(path)
        except (ValueError, OSError, EOFError, KeyError) as exc:
            raise InvalidUpload(str(exc)) from exc
        files[kind] = path
    telemetry = None
    if row.get('performance_events_key'):
        data = read_verified(store, row, 'performance_events', 4_000_000, generations)
        try:
            telemetry = validate_performance_events(json.loads(data), row['performance_events_count'])
        except (ValueError, HTTPException) as exc:
            raise InvalidUpload(str(getattr(exc, 'detail', exc))) from exc
    return files, telemetry, generations
