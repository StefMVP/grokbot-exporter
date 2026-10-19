"""Pull and verify a source export. Never extract a path outside the private destination."""
from __future__ import annotations
import base64
import hashlib
import json
from pathlib import Path, PurePosixPath
import os
import re
import stat
import zipfile
from .archive import update_manifest, write_private
from .capture import Browser


def safe_member(name: str) -> bool:
    name_path = PurePosixPath(name)
    return bool(name and not name_path.is_absolute() and '..' not in name_path.parts and '\\' not in name)


def extract(archive: Path, destination: Path) -> dict:
    with zipfile.ZipFile(archive) as bundle:
        for member in bundle.infolist():
            if not safe_member(member.filename) or stat.S_ISLNK(member.external_attr >> 16):
                raise ValueError('Unsafe archive member: ' + member.filename)
        inventory = json.loads(bundle.read('inventory.json'))
        expected = {entry['path']: entry for entry in inventory['files']}
        members = [item for item in bundle.infolist() if not item.is_dir() and item.filename != 'inventory.json']
        if len(expected) != len(inventory['files']) or len({item.filename for item in members}) != len(members):
            raise ValueError('Duplicate archive inventory entry')
        if {item.filename for item in members} != expected.keys():
            raise ValueError('Archive files differ from the inventory')
        for member in members:
            data = bundle.read(member)
            record = expected[member.filename]
            if len(data) != record['bytes'] or hashlib.sha256(data).hexdigest() != record['sha256']:
                raise ValueError('File checksum failed: ' + member.filename)
        for member in bundle.infolist():
            target = destination / member.filename
            if any(parent.is_symlink() for parent in [target, *target.parents]):
                raise ValueError('Archive destination contains a symlink')
        destination.mkdir(mode=0o700, parents=True, exist_ok=True)
        for member in bundle.infolist():
            if member.is_dir():
                continue
            data = bundle.read(member)
            target = destination / member.filename
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            fd = os.open(target, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
            with os.fdopen(fd, 'wb') as stream:
                stream.write(data)
    return inventory


def pull_cloud(session: str, remote: str, output: Path, bot_id: str) -> dict:
    browser = Browser(session)
    response = browser.read('text', id=bot_id, path=remote + '/manifest.json')
    if not response or response.get('truncated'):
        raise ValueError('The cloud export manifest is absent or incomplete')
    manifest = json.loads(response['text'])
    if manifest.get('format') != 'grokbot.export.cloud' or manifest.get('version') != 1:
        raise ValueError('Unsupported cloud export format')
    write_private(output / 'cloud-manifest.json', manifest)
    count = manifest['partCount']
    if not re.fullmatch('[0-9a-f]{64}', manifest['sha256']):
        raise ValueError('Invalid cloud checksum')
    if not isinstance(count, int) or count < 1 or count > 1_000_000:
        raise ValueError('Invalid part count')
    parts = output / ('cloud-parts-' + manifest['sha256'])
    parts.mkdir(mode=0o700, parents=True, exist_ok=True)
    for first in range(0, count, 4):
        numbers = [number for number in range(first, min(first + 4, count)) if not (parts / f'{number:06d}').exists()]
        if not numbers:
            continue
        paths = [f'{remote}/part-{number:06d}.txt' for number in numbers]
        responses = browser.read('texts', id=bot_id, paths=paths)
        for number, result in zip(numbers, responses, strict=True):
            value = result.get('value')
            if not value or value.get('kind') != 'text' or value.get('truncated'):
                raise ValueError(f'Part {number} is absent or incomplete')
            data = base64.b64decode(value['text'], validate=True)
            target = parts / f'{number:06d}'
            fd = os.open(target, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
            with os.fdopen(fd, 'wb') as stream:
                stream.write(data)
        print(json.dumps(dict(parts=min(first + 4, count), total=count)), flush=True)
    archive = output / 'cloud.zip'
    checksum = hashlib.sha256()
    with archive.open('wb') as stream:
        for number in range(count):
            data = (parts / f'{number:06d}').read_bytes()
            checksum.update(data)
            stream.write(data)
    archive.chmod(0o600)
    if archive.stat().st_size != manifest['bytes'] or checksum.hexdigest() != manifest['sha256']:
        raise ValueError('Cloud archive checksum failed; discard the part cache and retry')
    inventory = extract(archive, output / 'cloud')
    errors = manifest['errors'] + [item for item in inventory['errors'] if item not in manifest['errors']]
    proof = dict(ok=not errors, files=len(inventory['files']), bytes=manifest['bytes'],
                 sha256=checksum.hexdigest(), errors=errors)
    write_private(output / 'cloud-proof.json', proof)
    update_manifest(output)
    return proof
