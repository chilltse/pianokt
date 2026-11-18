import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.parse import urlparse

class Objects:
    """Create-only storage. Retries with identical bytes succeed."""
    def __init__(self,root):
        self.root=root.rstrip('/'); self.bucket=None
        if root.startswith('gs://'):
            from google.cloud import storage
            p=urlparse(root); self.prefix=p.path.strip('/')
            self.bucket=storage.Client().bucket(p.netloc)
        else: self.path=Path(root).resolve()

    def _name(self,key):
        if key.startswith('/') or '..' in key.split('/'): raise ValueError('Unsafe object key')
        return '/'.join(x for x in (getattr(self,'prefix',''),key) if x)

    def put(self,key,data):
        name=self._name(key)
        if self.bucket:
            from google.api_core.exceptions import PreconditionFailed
            blob=self.bucket.blob(name)
            try: blob.upload_from_string(data,if_generation_match=0)
            except PreconditionFailed:
                if blob.download_as_bytes()!=data: raise ValueError('Immutable object conflict')
        else:
            p=self.path/name; p.parent.mkdir(parents=True,exist_ok=True)
            with NamedTemporaryFile(dir=p.parent,delete=False) as f:
                tmp=Path(f.name); f.write(data); f.flush(); os.fsync(f.fileno())
            try: os.link(tmp,p)
            except FileExistsError:
                if p.read_bytes()!=data: raise ValueError('Immutable object conflict')
            finally: tmp.unlink(missing_ok=True)
        return self.root+'/'+key

    def read(self,key):
        name=self._name(key)
        return self.bucket.blob(name).download_as_bytes() if self.bucket else (self.path/name).read_bytes()

    def keys(self,prefix):
        if self.bucket:
            base=len(self.prefix)+1 if self.prefix else 0
            return sorted(b.name[base:] for b in self.bucket.list_blobs(prefix=self._name(prefix)))
        return sorted(str(p.relative_to(self.path)) for p in (self.path/prefix).rglob('*') if p.is_file())

    def json(self,key,data):
        return self.put(key,json.dumps(data,sort_keys=True,ensure_ascii=False,allow_nan=False,separators=(',',':')).encode())
