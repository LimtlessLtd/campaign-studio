"""Check, migrate or restore a campaign's data schema.

python DM/migrate.py                            report the version and documents a migration changes
python DM/migrate.py --apply                    back up and migrate
python DM/migrate.py --restore DM/backups/NAME  return documents to a pre-migration backup

The server migrates automatically when it starts. Stop it before using --apply or --restore. An
interrupted change is completed only by the server, because completing it can queue a render.
"""

import argparse
import socket
import sys

import campaign
import campaign_core
import schema


def server_running():
    try:
        with socket.create_connection(('127.0.0.1', campaign_core.PORT), timeout=0.5):
            return True
    except OSError:
        return False


def main(argv=None):
    parser = argparse.ArgumentParser(description='Campaign Studio data schema tool.')
    action = parser.add_mutually_exclusive_group()
    action.add_argument('--apply', action='store_true', help='back up and migrate')
    action.add_argument('--restore', metavar='BACKUP', help='restore a migration backup folder')
    args = parser.parse_args(argv)
    here = campaign.active()
    data, maps = here.data, here.maps
    if (args.apply or args.restore) and server_running():
        print(f'Stop Campaign Studio on port {campaign_core.PORT} first.', file=sys.stderr)
        return 1
    try:
        if args.restore:
            manifest = schema.restore(args.restore, data, maps)
            print(
                f'Restored {len(manifest["files"])} documents at schema {manifest["version"]}. '
                'Run the Campaign Studio version that matches this schema.'
            )
            return 0
        if campaign_core.JOURNAL.entries():
            # Completing it can queue a render, which only a running server carries out.
            message = 'An interrupted change is waiting. Start Campaign Studio: it completes the change, then migrates.'
            if args.apply:
                print(message, file=sys.stderr)
                return 1
            print(message)
        result = schema.migrate(data, maps, here.backups, dry_run=not args.apply)
    except (schema.SchemaError, OSError) as error:
        print('Error: ' + str(error), file=sys.stderr)
        return 1
    print(f'Data schema: {result.get("from", result["version"])} (this build: {schema.CURRENT})')
    if result['status'] == 'needs migration':
        print(f'Migration needed. {len(result["changes"])} documents will be updated:')
        print(''.join('  ' + name + '\n' for name in result['changes']), end='')
        print('Run with --apply, or start Campaign Studio, to back up and migrate.')
    elif result['status'] == 'migrated':
        print(f'Migrated {len(result["changes"])} documents. Backup: {result["backup"] or "none"}')
    else:
        print('No migration needed.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
