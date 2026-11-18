import json
import pytest
from pianokt_backend.storage import Objects
from pianokt_backend.publication import publish_bundle, read_bundle


def test_partial_bundle_is_invisible_and_same_plan_recovers(tmp_path):
    store=Objects(str(tmp_path)); original=store.put
    def fail(key,data):
        if key.endswith('/b'): raise OSError('injected crash')
        return original(key,data)
    store.put=fail
    with pytest.raises(OSError): publish_bundle(store,'snapshot',{'a':b'a','b':b'b'},{'version':1})
    with pytest.raises(FileNotFoundError): read_bundle(store,'snapshot')
    store.put=original
    with pytest.raises(ValueError,match='conflict'):
        publish_bundle(store,'snapshot',{'a':b'changed','b':b'b'},{'version':2})
    publish_bundle(store,'snapshot',{'a':b'a','b':b'b'},{'version':1})
    assert read_bundle(store,'snapshot')[1]=={'a':b'a','b':b'b'}
    (tmp_path/'snapshot'/'b').write_bytes(b'corruption')
    with pytest.raises(ValueError,match='integrity'): read_bundle(store,'snapshot')
