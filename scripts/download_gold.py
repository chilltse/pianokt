"""Download and verify a formally published Gold bundle; checks withdrawal status."""
import argparse
import os
from pianokt_backend.online import connect
from pianokt_backend.storage import Objects
from pianokt_backend.publication import read_bundle, publish_bundle
from pianokt_backend.storage_layout import gold_dataset_prefix


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset-type', required=True, choices=('attempts', 'knowledge_tracing'))
    parser.add_argument('--dataset-version', required=True)
    parser.add_argument('--output-root', required=True)
    args = parser.parse_args()
    prefix = gold_dataset_prefix(args.dataset_type, args.dataset_version)
    manifest, files = read_bundle(Objects(os.environ['DATA_ROOT']), prefix)
    if manifest.get('data_architecture_version') != '3' or manifest.get('dataset_version') != args.dataset_version:
        raise ValueError('Dataset identity mismatch')
    with connect() as c:
        if c.execute("select 1 from public.piano_data_exclusions where source_environment=%s and scope='dataset' and identity=%s", (manifest['source_environment'], args.dataset_version)).fetchone():
            raise ValueError('Dataset withdrawn')
    publish_bundle(Objects(args.output_root), prefix, files, {k:v for k,v in manifest.items() if k not in ('files','publication_protocol')})
    print(prefix)

if __name__ == '__main__': main()
