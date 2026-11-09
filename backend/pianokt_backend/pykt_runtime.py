"""Explicit development wiring, authenticated-user scoping, and compatibility verification."""
from contextlib import contextmanager
import json
import os
from pathlib import Path

import httpx

from .pykt_catalog import CatalogCandidateProvider, CATALOG_SQL
from .pykt_history import development, read_snapshot, binding_path, verify_binding
from .pykt_integration import Compatibility, CompatibilityError, PyKTClient, canonical
from .pykt_recommendation import RecommendationConfig, RecommendationService
from .pykt_verify import verify_fixture


def environment():
    value = development(os.environ.get('PIANOKT_ENV'))
    if os.environ.get('PYKT_DEV_ENABLED') != '1':
        raise CompatibilityError('Set PYKT_DEV_ENABLED=1 explicitly')
    return value


def database_guard():
    environment()
    # Avoid accidentally using the application's default/production DB connection.
    if not os.environ.get('PYKT_DEV_DATABASE_URL') or os.environ.get('DATABASE_URL') != os.environ['PYKT_DEV_DATABASE_URL']:
        raise CompatibilityError('DATABASE_URL must equal explicitly configured PYKT_DEV_DATABASE_URL')


@contextmanager
def verified_client():
    environment()
    token = os.environ.get('PYKT_API_TOKEN')
    url = os.environ['PYKT_URL']
    fixture = Path(os.environ['PYKT_GOLDEN_FIXTURE'])
    manifest = json.loads(Path(os.environ['PYKT_COMPATIBILITY_MANIFEST']).read_text())
    with httpx.Client(base_url=url, timeout=60, trust_env=False,
                      headers={'Authorization': f'Bearer {token}'} if token else {}) as http:
        verified = verify_fixture(fixture, http)
        if canonical(manifest) != canonical(verified):
            raise CompatibilityError('Manifest differs from successful current golden verification')
        yield PyKTClient(http, Compatibility(**verified))


def snapshot_for_user(connect, objects, uid):
    database_guard()
    with connect() as c:
        c.execute('set transaction isolation level repeatable read read only')
        return read_snapshot(c, objects, uid, environment())


def recommend_for_user(uid, connect, objects):
    database_guard()
    with verified_client() as client:
        def check(state):
            current = snapshot_for_user(connect, objects, uid)
            if current['snapshot_digest'] != snapshot['snapshot_digest']:
                raise CompatibilityError('READY source snapshot changed; restart entire recommendation')
            path = binding_path(os.environ['PYKT_SYNC_DIR'], current['student_id'])
            if not path.is_file():
                raise CompatibilityError('History sync provenance missing; run explicit sync CLI')
            return verify_binding(json.loads(path.read_text()), current, state, client)
        snapshot = snapshot_for_user(connect, objects, uid)
        # Fail before catalog downloads when the history is not synchronized.
        check(client._state(snapshot['student_id']))
        with connect() as c:
            catalog = c.execute(CATALOG_SQL).fetchall()
        proofs = json.loads(Path(os.environ['PYKT_CATALOG_PROVENANCE']).read_text())
        config = RecommendationConfig(**json.loads(os.environ.get('PYKT_RECOMMENDATION_CONFIG', '{}')))
        # Separate unauthenticated public Storage client: never forward the PyKT token.
        with httpx.Client(timeout=15, trust_env=False) as storage_http:
            provider = CatalogCandidateProvider(storage_http, os.environ['SUPABASE_URL'], context_provenance=proofs)
            candidates = provider.load(catalog)
        response = RecommendationService(client, config).recommend(snapshot['student_id'], candidates, check_history=check)
        response['input_alignment_run_ids'] = [s['alignment_run_id'] for s in snapshot['sources'] if s['skip_reason'] is None]
        attempt_id = snapshot['attempts'][-1]['attempt_id'] if snapshot['attempts'] else None
        return response, attempt_id
