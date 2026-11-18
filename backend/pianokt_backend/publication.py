"""Logical atomic publication over create-only objects.

A prefix is reserved for exactly one immutable plan. Data files are not a
committed dataset until manifest.json exists and every recorded digest matches.
Failed writes are resumable with the same plan, never silently overwritten.
"""
import json
from .contracts import digest


def publish_bundle(objects, prefix, files, metadata):
    hashes={name:digest(data) for name,data in sorted(files.items())}
    manifest={**metadata,'files':hashes,'publication_protocol':'manifest-v1'}
    try:
        previous = json.loads(objects.read(prefix+'/intent.json'))
    except FileNotFoundError:
        previous = None
    except Exception as exc:
        from google.api_core.exceptions import NotFound
        if not isinstance(exc, NotFound):
            raise
        previous = None
    if previous is not None:
        # The first publisher owns wall-clock metadata. Retries compare every
        # content/selection field while retaining that original timestamp.
        if {k:v for k,v in previous.items() if k!='created_at'} != {k:v for k,v in manifest.items() if k!='created_at'}:
            raise ValueError('Immutable dataset intent conflict')
        manifest = previous
    objects.json(prefix+'/intent.json',manifest)
    for name,data in sorted(files.items()):
        objects.put(prefix+'/'+name,data)
        if digest(objects.read(prefix+'/'+name)) != hashes[name]:
            raise ValueError('Dataset write verification failed: ' + name)
    objects.json(prefix+'/manifest.json',manifest)
    return manifest


def read_bundle(objects,prefix):
    manifest=json.loads(objects.read(prefix+'/manifest.json'))
    if manifest.get('publication_protocol')!='manifest-v1':
        raise ValueError('Unsupported publication protocol')
    if json.loads(objects.read(prefix+'/intent.json')) != manifest:
        raise ValueError('Dataset intent/manifest mismatch')
    files={}
    for name,expected in manifest['files'].items():
        if '/' in name or name in ('.','..','intent.json','manifest.json'):
            raise ValueError('Unsafe dataset filename')
        data=objects.read(prefix+'/'+name)
        if digest(data)!=expected: raise ValueError('Dataset integrity mismatch: '+name)
        files[name]=data
    return manifest,files
