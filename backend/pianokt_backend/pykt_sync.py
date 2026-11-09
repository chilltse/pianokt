"""Explicit development/test history sync. Default is a read-only dry-run."""
import argparse
import json
import os
from uuid import UUID

from .online import connect
from .storage import Objects
from .pykt_runtime import database_guard, snapshot_for_user, verified_client
from .pykt_history import sync_history


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--user-id', type=UUID, required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    database_guard()
    with verified_client() as client:
        store = Objects(os.environ['RAW_ROOT'])
        snapshot = snapshot_for_user(connect, store, str(args.user_id))
        result = sync_history(client, snapshot, os.environ['PYKT_SYNC_DIR'], apply=args.apply)
        # A new READY attempt during sync invalidates binding; never report success for a stale snapshot.
        if snapshot_for_user(connect, store, str(args.user_id))['snapshot_digest'] != snapshot['snapshot_digest']:
            raise ValueError('READY history changed during sync; rerun against the new snapshot')
        print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
