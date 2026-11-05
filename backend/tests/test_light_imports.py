import base64
from io import BytesIO
import os
import subprocess
import sys
import textwrap
from pathlib import Path

from fastapi.testclient import TestClient
import mido
import pytest
from pianokt_backend import api


def fresh_python(code):
    environment = dict(os.environ, PYTHONPATH=str(Path(__file__).parents[1]))
    return subprocess.run([sys.executable, '-c', textwrap.dedent(code)], env=environment,
                          capture_output=True, text=True, check=True, timeout=30)


@pytest.mark.parametrize('module', ['api', 'outbox_dispatch', 'worker_api'])
def test_light_modules_do_not_load_alignment_or_publisher(module):
    fresh_python(f'''
        import pianokt_backend.{module}
        import sys
        assert not any(name in sys.modules for name in (
            'numpy','pandas','scipy','pyarrow','pianokt_backend.alignment.service',
            'google.cloud.pubsub_v1'))
    ''')


def test_cli_relay_uses_light_module_and_preserves_arguments():
    fresh_python('''
        import sys
        from pianokt_backend import outbox_dispatch, cli
        calls=[]
        outbox_dispatch.relay=lambda limit, **kw: calls.append((limit,kw)) or 0
        sys.argv=['pianokt-data','relay','--limit','7','--max-seconds','9']
        cli.main()
        assert calls == [(7, {'max_duration_s':9})]
        assert 'pianokt_backend.worker' not in sys.modules
        assert 'numpy' not in sys.modules
    ''')
    result = fresh_python('''
        import sys
        from pianokt_backend import cli
        sys.argv=['pianokt-data','relay','--help']
        cli.main()
    ''')
    assert '--limit' in result.stdout


def test_disabled_lab_does_not_load_alignment():
    fresh_python('''
        import sys,os
        from fastapi.testclient import TestClient
        from pianokt_backend.api import app
        os.environ['PIANOKT_ENV']='production'
        os.environ['PIANOKT_LOCAL_ALIGNMENT_LAB']='1'
        r=TestClient(app).post('/debug/local-align',json={'performance_midi_base64':'AA==','reference_midi_base64':'AA==','observed_end_sec':2,'reference_origin_sec':0})
        assert r.status_code==404
        assert 'pianokt_backend.alignment.service' not in sys.modules
    ''')


def test_enabled_lab_still_aligns(monkeypatch, tmp_path):
    monkeypatch.setenv('PIANOKT_ENV', 'development')
    monkeypatch.setenv('PIANOKT_LOCAL_ALIGNMENT_LAB', '1')
    monkeypatch.setenv('PIANOKT_LOCAL_ALIGNMENT_OUTPUT_DIR', str(tmp_path))
    monkeypatch.delenv('K_SERVICE', raising=False)
    midi = mido.MidiFile()
    track = mido.MidiTrack(); midi.tracks.append(track)
    for pitch in (60, 62, 64):
        track.append(mido.Message('note_on', note=pitch, velocity=80, time=0))
        track.append(mido.Message('note_off', note=pitch, time=480))
    stream = BytesIO(); midi.save(file=stream)
    data = base64.b64encode(stream.getvalue()).decode()
    response = TestClient(api.app).post('/debug/local-align', json={
        'performance_midi_base64': data, 'reference_midi_base64': data, 'observed_end_sec': 2, 'reference_origin_sec': 0})
    assert response.status_code == 200
    assert response.json()['alignment_run_id']
    assert Path(response.json()['local_output_path']).is_file()
