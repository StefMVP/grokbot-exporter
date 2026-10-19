"""Private, target-independent export files and integrity checks."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any
from urllib.parse import unquote, urlparse


def write_private(dest: Path, value: Any) -> None:
    dest.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + '.tmp')
    fd = os.open(tmp, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
    tmp.replace(dest)


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def source_id(value: str) -> str:
    if not re.fullmatch(r'[A-Za-z0-9_-]+', value):
        raise ValueError('Invalid source bot ID')
    return value


def source_file(source: str, cloud: Path) -> Path | None:
    source = unquote(urlparse(source).path) if source.startswith('file:') else source
    for prefix, target in [('/home/box/agent-data/', cloud), ('/home/box/sand-data/', cloud),
                           ('/workspace/', cloud / 'workspace'), ('/home/box/reference/', cloud / 'reference')]:
        if source.startswith(prefix):
            relative = Path(source[len(prefix):])
            if relative.is_absolute() or '..' in relative.parts:
                return None
            candidate = target / relative
            if not candidate.resolve().is_relative_to(cloud.resolve()):
                return None
            return candidate
    return None


def export_files(root: Path) -> list[Path]:
    names = ['roster.json', 'capture-summary.json', 'cloud-manifest.json', 'cloud-proof.json', 'file-recovery-proof.json']
    files = [root / name for name in names if (root / name).is_file()]
    files.extend(root.glob('*-setup.json'))
    files.extend(root.glob('*-transcript.json'))
    files.extend(item for item in (root / 'cloud').rglob('*') if item.is_file())
    for item in files:
        if item.is_symlink() or not item.resolve().is_relative_to(root.resolve()):
            raise ValueError('Export contains a linked file: ' + str(item.relative_to(root)))
    return sorted(files)


def update_manifest(root: Path) -> dict:
    roster = json.loads((root / 'roster.json').read_text())
    bots = []
    errors = []
    for bot in roster['agents']:
        key = source_id(bot['id'])
        setup_file, history_file = root / f'{key}-setup.json', root / f'{key}-transcript.json'
        setup = json.loads(setup_file.read_text()) if setup_file.exists() else {}
        history = json.loads(history_file.read_text()) if history_file.exists() else {}
        complete = bool(history.get('complete') and setup.get('skills', {}).get('ok') and setup.get('routines', {}).get('ok'))
        if not complete:
            errors.append(dict(bot=key, error=history.get('failure') or 'Incomplete history or setup'))
        for name, result in setup.items():
            if isinstance(result, dict) and result.get('ok') is False:
                errors.append(dict(bot=key, operation=name, error=result.get('error', 'Read failed')))
        bots.append(dict(id=key, name=bot['name'], complete=complete, entries=len(history.get('entries', []))))
    for name in ['cloud-proof.json', 'file-recovery-proof.json']:
        if (root / name).exists():
            proof = json.loads((root / name).read_text())
            if not proof.get('ok'):
                errors.append(dict(operation=name, error='Some files are unavailable', details=proof.get('errors', proof.get('missing', []))))
    files = [dict(path=str(item.relative_to(root)), bytes=item.stat().st_size,
                  sha256=hashlib.sha256(item.read_bytes()).hexdigest()) for item in export_files(root)]
    manifest = dict(format='grokbot.export', version=1, source=dict(product='Grok Bot', appVersion=roster.get('appVersion'),
                    adapterTestedWith='0.70.2'), capturedAt=roster.get('capturedAt'),
                    updatedAt=datetime.now(timezone.utc).isoformat(), complete=not errors, bots=bots, errors=errors, files=files)
    write_private(root / 'manifest.json', manifest)
    return manifest


def verify_export(root: Path) -> dict:
    manifest = json.loads((root / 'manifest.json').read_text())
    if manifest.get('format') != 'grokbot.export' or manifest.get('version') != 1:
        raise ValueError('Unsupported export format')
    actual = {str(item.relative_to(root)): item for item in export_files(root)}
    expected = {item['path']: item for item in manifest['files']}
    if len(expected) != len(manifest['files']) or actual.keys() != expected.keys():
        raise ValueError('Export files differ from the manifest')
    for name, item in actual.items():
        record = expected[name]
        if item.stat().st_size != record['bytes'] or hashlib.sha256(item.read_bytes()).hexdigest() != record['sha256']:
            raise ValueError('Export checksum failed: ' + name)
    return manifest
