"""Recover referenced files absent from the cloud snapshot through native readers."""
from __future__ import annotations
import base64
import hashlib
import json
import os
from pathlib import Path
from .archive import source_file, update_manifest, write_private
from .capture import Browser


def recover(session: str, source: Path) -> dict:
    browser = Browser(session)
    roster = json.loads((source / 'roster.json').read_text())
    records = []
    missing = []
    for bot in roster['agents']:
        history = json.loads((source / f'{bot["id"]}-transcript.json').read_text())
        for entry in history['entries']:
            message = entry.get('message', {})
            if entry['kind'] != 'user-attachment' and message.get('type') != 'attachment':
                continue
            location = entry.get('file_path') or message.get('url', '')
            dest = source_file(location, source / 'cloud')
            if dest is None:
                missing.append(dict(bot=bot['name'], source=location, reason='unmapped path'))
                continue
            if dest.is_file():
                continue
            response = browser.read('attachment', id=bot['id'], path=location)
            data = None
            reader = 'attachment'
            if response and response.get('kind') == 'bytes':
                candidate = base64.b64decode(response['base64'], validate=True)
                if len(candidate) == response['size']:
                    data = candidate
            if data is None and dest.suffix.lower() in ['.png', '.jpg', '.jpeg', '.gif', '.webp']:
                response = browser.read('image', id=bot['id'], path=location)
                reader = 'original-image'
                url = response.get('src', '') if isinstance(response, dict) else ''
                if url.startswith('data:image/') and ';base64,' in url:
                    data = base64.b64decode(url.split(';base64,', 1)[1], validate=True)
            if data is None:
                missing.append(dict(bot=bot['name'], source=location, reason='native readers returned no complete file'))
                continue
            if any(parent.is_symlink() for parent in [dest, *dest.parents]):
                raise ValueError('Recovery destination contains a symlink')
            dest.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            fd = os.open(dest, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, 'wb') as stream:
                stream.write(data)
            records.append(dict(bot=bot['name'], source=location, bytes=len(data), sha256=hashlib.sha256(data).hexdigest(), reader=reader))
    proof = dict(ok=not missing, recovered=records, missing=missing)
    write_private(source / 'file-recovery-proof.json', proof)
    update_manifest(source)
    return proof
