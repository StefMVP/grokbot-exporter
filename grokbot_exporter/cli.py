"""Export Grok Bot data, with an optional OpenMaus migration adapter."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from .archive import verify_export
from .capture import DEFAULT_DELAY, MIN_DELAY, capture
from .cloud import pull_cloud
from .recover import recover
from .source import launch, stop


def main() -> None:
    parser = argparse.ArgumentParser(description='Export Grok Bot chats, profiles, skills, routines and files')
    sub = parser.add_subparsers(dest='command', required=True)
    command = sub.add_parser('capture', help='Save raw data from a signed-in Grok Bot session')
    command.add_argument('--session', required=True)
    command.add_argument('--output', required=True, type=Path)
    command.add_argument('--only', action='append')
    command.add_argument('--delay', type=float, default=DEFAULT_DELAY,
                         help=f'Seconds between reads, at least {MIN_DELAY} (default {DEFAULT_DELAY})')
    command = sub.add_parser('pull-cloud', help='Download and check a private cloud export')
    command.add_argument('--session', required=True)
    command.add_argument('--remote', default='/workspace/.grokbot-export')
    command.add_argument('--output', required=True, type=Path)
    command.add_argument('--bot-id', required=True)
    command.add_argument('--delay', type=float, default=DEFAULT_DELAY,
                         help=f'Seconds between reads, at least {MIN_DELAY} (default {DEFAULT_DELAY})')
    command = sub.add_parser('recover-files', help='Recover chat files absent from the cloud export')
    command.add_argument('--session', required=True)
    command.add_argument('--source', required=True, type=Path)
    command.add_argument('--delay', type=float, default=DEFAULT_DELAY,
                         help=f'Seconds between reads, at least {MIN_DELAY} (default {DEFAULT_DELAY})')
    command = sub.add_parser('check', help='Check a saved export without a browser or target app')
    command.add_argument('--source', required=True, type=Path)
    command = sub.add_parser('launch-source', help='Start a separate headless Grok process with a private profile copy (Linux)')
    command.add_argument('--app', required=True, type=Path)
    command.add_argument('--profile', required=True, type=Path)
    command.add_argument('--output', required=True, type=Path)
    command.add_argument('--session', default='grokbot-exporter')
    command = sub.add_parser('stop-source', help='Stop the source process named in its receipt')
    command.add_argument('--output', required=True, type=Path)
    adapter = sub.add_parser('openmaus', help='Convert, import and verify with the optional OpenMaus adapter')
    native = adapter.add_subparsers(dest='operation', required=True)
    command = native.add_parser('build', help='Convert a saved export to an OpenMaus bundle')
    command.add_argument('--source', required=True, type=Path)
    command.add_argument('--output', required=True, type=Path)
    command.add_argument('--host-root', required=True, type=Path)
    command.add_argument('--policy', type=Path)
    command = native.add_parser('apply', help='Import a bundle into an explicit loopback OpenMaus URL')
    command.add_argument('--bundle', required=True, type=Path)
    command.add_argument('--config', required=True, type=Path)
    command.add_argument('--url', required=True)
    command.add_argument('--receipt', required=True, type=Path)
    command.add_argument('--only', action='append')
    command = native.add_parser('verify', help='Read back and compare imported data')
    command.add_argument('--bundle', required=True, type=Path)
    command.add_argument('--url', required=True)
    command.add_argument('--receipt', required=True, type=Path)
    command.add_argument('--complete', action='store_true')
    command = native.add_parser('cutover', help='Pause or restore source schedules after a complete target check')
    command.add_argument('--session', required=True)
    command.add_argument('--bundle', required=True, type=Path)
    command.add_argument('--proof', required=True, type=Path)
    command.add_argument('--receipt', required=True, type=Path)
    command.add_argument('--restore', action='store_true')
    args = parser.parse_args()
    for name, value in vars(args).items():
        if isinstance(value, Path):
            setattr(args, name, value.expanduser().resolve())
    try:
        if args.command == 'launch-source':
            result = launch(args.app, args.profile, args.output, args.session)
        elif args.command == 'stop-source':
            result = stop(args.output)
        elif args.command == 'capture':
            result = capture(args.session, args.output, args.only, args.delay)
        elif args.command == 'pull-cloud':
            result = pull_cloud(args.session, args.remote, args.output, args.bot_id, args.delay)
        elif args.command == 'recover-files':
            result = recover(args.session, args.source, args.delay)
        elif args.command == 'check':
            result = verify_export(args.source)
            if not result['complete']:
                raise ValueError('Export is incomplete; read manifest.json for details')
        else:
            result = openmaus(args)
    except (OSError, ValueError, RuntimeError) as error:
        parser.exit(2, f'{parser.prog}: {error}\n')
    print(json.dumps(result, ensure_ascii=False, indent=2))


def openmaus(args: argparse.Namespace) -> dict:
    from .adapters.openmaus.apply import apply, verify
    from .adapters.openmaus.bundle import build
    from .adapters.openmaus.cutover import cutover
    if args.operation == 'build':
        return build(args.source, args.output, args.host_root, json.loads(args.policy.read_text()) if args.policy else None)
    if args.operation == 'apply':
        receipt = apply(args.bundle, args.config, args.url, args.receipt, args.only)
        return dict(importedBots=len(receipt['bots']), sourceBots=receipt['sourceBots'], receipt=str(args.receipt))
    if args.operation == 'cutover':
        return cutover(args.session, args.bundle, args.proof, args.receipt, args.restore)
    return verify(args.bundle, args.url, args.receipt, args.complete)


if __name__ == '__main__':
    main()
