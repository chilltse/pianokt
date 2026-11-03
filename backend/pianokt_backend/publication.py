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
    objects.json(prefix+'/intent.json',manifest)
    for name,data in sorted(files.items()):
        objects.put(prefix+'/'+name,data)
    objects.json(prefix+'/manifest.json',manifest)
    return manifest


def read_bundle(objects,prefix):
    manifest=json.loads(objects.read(prefix+'/manifest.json'))
    if manifest.get('publication_protocol')!='manifest-v1':
        raise ValueError('Unsupported publication protocol')
    files={}
    for name,expected in manifest['files'].items():
        data=objects.read(prefix+'/'+name)
        if digest(data)!=expected: raise ValueError('Dataset integrity mismatch: '+name)
        files[name]=data
    return manifest,files
