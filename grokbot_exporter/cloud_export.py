"""Standalone cloud script. Read source files and write a private export folder."""
from __future__ import annotations
import base64
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import zipfile

EXCLUDED_DIRS = {'.git', 'node_modules', '__pycache__', '.venv', '.cache'}
EXCLUDED_NAMES = {'host-secrets.json', 'box-secrets.json', 'sand-secrets.json', 'gateway.json', 'teach-queue-key.json'}


def selected(folder: Path, output: Path):
    for current, dirs, files in os.walk(folder, followlinks=False):
        dirs[:] = [name for name in dirs if name not in EXCLUDED_DIRS
                   and not name.startswith(('.grokbot-export', '.venv', 'venv-'))
                   and not (Path(current) / name).is_symlink()
                   and (Path(current) / name).resolve() != output.resolve()]
        for name in files:
            source = Path(current) / name
            if source.is_symlink() or name in EXCLUDED_NAMES or name == '.env' or name.startswith('.env.'):
                continue
            if name.endswith(('.db-wal', '.db-shm', '.lock')):
                continue
            yield source


def json_value(value):
    return {'base64': base64.b64encode(value).decode()} if isinstance(value, bytes) else value


def export_cloud(output: Path, source: Path, workspace: Path, reference: Path) -> dict:
    output.mkdir(mode=0o700, parents=True, exist_ok=True)
    output.chmod(0o700)
    inventory, errors = [], []
    archive = output / 'cloud.zip'
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
        def add(name: str, data: bytes):
            bundle.writestr(name, data)
            inventory.append(dict(path=name, bytes=len(data), sha256=hashlib.sha256(data).hexdigest()))

        roots = [(source / name, name) for name in ['agents', 'workflows', 'plugin-skills', 'managed-skills']]
        roots.extend([(workspace, 'workspace'), (reference, 'reference')])
        for folder, prefix in roots:
            if not folder.is_dir():
                continue
            for item in selected(folder, output):
                name = prefix + '/' + str(item.relative_to(folder))
                snapshot_file = None
                try:
                    if item.suffix == '.db':
                        snapshot_file = output / ('snapshot-' + hashlib.sha256(name.encode()).hexdigest() + '.db')
                        original = sqlite3.connect(item.as_uri() + '?mode=ro', uri=True)
                        snapshot = sqlite3.connect(snapshot_file)
                        try:
                            original.backup(snapshot)
                            snapshot.row_factory = sqlite3.Row
                            tables = {}
                            for row in snapshot.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
                                quoted = '"' + row[0].replace('"', '""') + '"'
                                tables[row[0]] = [{key: json_value(value) for key, value in dict(record).items()}
                                                  for record in snapshot.execute('SELECT * FROM ' + quoted)]
                            add(name + '.json', json.dumps(tables, ensure_ascii=False).encode())
                        finally:
                            snapshot.close()
                            original.close()
                        data = snapshot_file.read_bytes()
                    else:
                        data = item.read_bytes()
                    add(name, data)
                except Exception as error:
                    errors.append(dict(path=name, error=type(error).__name__))
                finally:
                    if snapshot_file:
                        snapshot_file.unlink(missing_ok=True)
        bundle.writestr('inventory.json', json.dumps(dict(files=inventory, errors=errors), indent=2))
    archive.chmod(0o600)
    parts, checksum = 0, hashlib.sha256()
    with archive.open('rb') as stream:
        while data := stream.read(4 * 1024 * 1024):
            checksum.update(data)
            dest = output / f'part-{parts:06d}.txt'
            dest.write_text(base64.b64encode(data).decode())
            dest.chmod(0o600)
            parts += 1
    manifest = dict(format='grokbot.export.cloud', version=1, bytes=archive.stat().st_size, sha256=checksum.hexdigest(),
                    partCount=parts, files=len(inventory), errors=errors)
    dest = output / 'manifest.json'
    dest.write_text(json.dumps(manifest, indent=2))
    dest.chmod(0o600)
    return manifest


def main():
    manifest = export_cloud(Path(os.environ.get('GROKBOT_EXPORT_OUTPUT', '/workspace/.grokbot-export')),
                            Path('/home/box/agent-data'), Path('/workspace'), Path('/home/box/reference'))
    print(json.dumps(manifest))


if __name__ == '__main__':
    main()
