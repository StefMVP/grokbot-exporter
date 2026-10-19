"""Apply a checked bundle to a local OpenMaus server. Never replay source requests."""
from __future__ import annotations
import hashlib
import json
import mimetypes
import html
import copy
from pathlib import Path
from urllib.parse import urlparse, urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError
import uuid
from ...archive import digest, write_private

FILE_TYPES = {'.md', '.txt', '.csv', '.tsv', '.json', '.pdf', '.rtf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx', '.odt', '.ods', '.odp', '.mp4', '.webm', '.mov', '.mp3', '.m4a', '.aac', '.wav', '.ogg', '.opus', '.flac'}
IMAGE_TYPES = {'.png', '.jpg', '.jpeg', '.gif', '.webp'}


class LocalMaus:
    def __init__(self, url: str):
        parsed = urlparse(url)
        if parsed.scheme != 'http' or parsed.hostname not in ['127.0.0.1', 'localhost', '::1'] or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ['', '/']:
            raise ValueError('Run the importer on the OpenMaus host and pass its loopback http URL')
        self.url = url.rstrip('/')

    def request(self, method: str, route: str, body: object = None, raw: bytes | None = None, mime: str | None = None):
        data = raw if raw is not None else json.dumps(body).encode() if body is not None else None
        headers = {'Content-Type': mime or 'application/json'}
        request = Request(self.url + route, data=data, headers=headers, method=method)
        try:
            with urlopen(request, timeout=120) as response:
                return json.load(response)
        except HTTPError as error:
            # Request bodies and credentials never enter diagnostic output.
            text = error.read().decode(errors='replace')
            raise RuntimeError(f'OpenMaus returned {error.code} for {method} {route}: {text[:2000]}') from None

    def download(self, route: str, body: object) -> bytes:
        request = Request(self.url + route, data=json.dumps(body).encode(), headers={'Content-Type': 'application/json'}, method='POST')
        with urlopen(request, timeout=120) as response:
            return response.read()

    def messages(self, thread: str) -> list[dict]:
        result = []
        before = None
        while True:
            params = {'limit': 1000, **({'before': before} if before else {})}
            page = self.request('GET', f'/api/threads/{thread}/messages?' + urlencode(params))
            result = page['messages'] + result
            if not page.get('hasMore'):
                return result
            if not page['messages'] or page['messages'][0]['id'] == before:
                raise RuntimeError('Target history pagination made no progress')
            before = page['messages'][0]['id']


def apply(bundle_file: Path, config_file: Path, url: str, receipt_file: Path, only: list[str] | None = None) -> dict:
    bundle = json.loads(bundle_file.read_text())
    if bundle.get('format') != 'grokbot.openmaus' or bundle.get('version') != 1:
        raise ValueError('Unsupported migration bundle')
    config = json.loads(config_file.read_text())
    for bot in bundle['bots']:
        if only and bot['name'] not in only and bot['key'] not in only:
            continue
        for routine in bot['routines']:
            if not routine['replacement'] and routine['schedule'] is None:
                raise ValueError('Map the webhook to a local receiver before apply: ' + routine['name'])
        for file in bot['files']:
            if not file['recovered']:
                raise ValueError('Source file is missing: ' + file['name'])
            data = Path(file['path']).read_bytes()
            if hashlib.sha256(data).hexdigest() != file['sha256']:
                raise ValueError('Source file checksum differs: ' + file['name'])
        if not config.get('bindings', {}).get(bot['name']) and not config.get('modelSelection'):
            raise ValueError('Set modelSelection in the OpenMaus config')
    target = LocalMaus(url)
    receipt = json.loads(receipt_file.read_text()) if receipt_file.exists() else dict(target=target.url, namespace=bundle['namespace'], bots={})
    if receipt['target'] != target.url or receipt['namespace'] != bundle['namespace']:
        raise ValueError('The receipt belongs to a different target or namespace')
    before = target.request('GET', '/api/bots?messages=0')
    if not receipt_file.exists():
        backup = target.request('POST', '/api/teams/export', dict(format='backup', name='Before Grok migration'))
        write_private(receipt_file.with_name(receipt_file.stem + '-before.json'), backup)
    for bot in bundle['bots']:
        if only and bot['name'] not in only and bot['key'] not in only:
            continue
        saved = receipt['bots'].setdefault(bot['key'], dict(skills={}, routines={}))
        uploaded = {}
        for file in bot['files']:
            if not file['recovered']:
                continue
            source = Path(file['path'])
            extension = source.suffix.lower()
            image = extension in IMAGE_TYPES
            if not image and extension not in FILE_TYPES:
                continue
            mime = mimetypes.guess_type(str(source))[0] or 'application/octet-stream'
            if extension == '.md':
                mime = 'text/markdown'
            upload_id = str(uuid.uuid5(uuid.NAMESPACE_URL, bundle['namespace'] + ':' + file['source']))
            route = '/api/attachments?' if image else '/api/files?'
            route += urlencode(dict(uploadId=upload_id, **({} if image else {'name': file['name']})))
            result = target.request('POST', route, raw=source.read_bytes(), mime=mime)
            uploaded[file['messageId']] = [dict(kind='image' if image else 'file', path=result['path'], mime=result.get('mime', mime), name=file['name'])]
        body = dict(namespace=bundle['namespace'], key=bot['key'], document=import_document(bot, uploaded),
                    attachments=uploaded, reactions=bot.get('reactions', {}), historyCwd=bundle['hostRoot'])
        binding = config.get('bindings', {}).get(bot['name'])
        if binding:
            body['bindBotId'] = binding
            if 'preserved' not in saved:
                current = next((item for item in before['bots'] if item['id'] == binding), None)
                if not current:
                    raise ValueError('A bound destination does not exist: ' + bot['name'])
                saved['preserved'] = {key: current.get(key) for key in ['name', 'title', 'description', 'soul', 'modelSelection', 'cwd', 'threadId', 'approvalMode', 'computer']}
                saved['preservedMemory'] = target.request('GET', f'/api/bots/{binding}/memory')['text']
                write_private(receipt_file, receipt)
        result = target.request('POST', '/api/migrations/bots', body)
        saved.update(botId=result['botId'], threads=result['threadIds'], entries=bot['entries'], attachments=uploaded)
        write_private(receipt_file, receipt)
        if not binding:
            profile = dict(**config.get('runtimeProfile', {}), cwd=config.get('botFolders', {}).get(bot['name'], bundle['hostRoot']),
                           modelSelection=config['modelSelection'], computer='off')
            if saved.get('profile'):
                current = next(item for item in target.request('GET', '/api/bots?messages=0')['bots'] if item['id'] == result['botId'])
                if any(current.get(key) != value for key, value in saved['profile'].items()):
                    raise ValueError('Keep changed local runtime settings: ' + bot['name'])
            target.request('PATCH', f'/api/bots/{result["botId"]}', profile)
            saved['profile'] = profile
        # The importer's default policy enables user workflows. Native tools replace vendor-managed wrappers.
        existing = {item['name']: item for item in target.request('GET', f'/api/bots/{result["botId"]}/skills')['skills']}
        for skill in bot['skills']:
            expected = {key: skill[key] for key in ['name', 'description', 'text', 'source', 'enabled']}
            marker = digest(expected)
            current = existing.get(skill['name'])
            if current:
                text = target.request('GET', f'/api/bots/{result["botId"]}/skills/{skill["name"]}')['text']
                if text == skill['text'] and current['enabled'] == skill['enabled']:
                    saved['skills'][skill['name']] = marker
                    continue
                previous = saved['skills'].get(skill['name'])
                if previous is None:
                    raise RuntimeError('A target skill already differs: ' + bot['name'] + '/' + skill['name'])
                raise RuntimeError('Keep the changed local skill and resolve its migration: ' + bot['name'] + '/' + skill['name'])
            target.request('POST', f'/api/bots/{result["botId"]}/skill-template', expected)
            saved['skills'][skill['name']] = marker
        write_private(receipt_file, receipt)
        existing_routines = target.request('GET', '/api/routines')['routines']
        for routine in bot['routines']:
            if routine['replacement']:
                saved['routines'][routine['sourceId']] = dict(replacement=routine['replacement'])
                continue
            content = dict(name=routine['name'], prompt=routine['prompt'], botId=result['botId'], target='bot',
                           runOn='maus', enabled=routine['enabled'], schedule=routine['schedule'],
                           durationMinutes=15, timeoutMinutes=60, continuity=True)
            matches = [item for item in existing_routines if item['botId'] == result['botId'] and item['name'] == routine['name']]
            if len(matches) > 1:
                raise RuntimeError('Duplicate target routines: ' + routine['name'])
            if matches:
                current = matches[0]
                if any(current.get(key) != value for key, value in content.items()):
                    raise RuntimeError('Target routine differs: ' + routine['name'])
                native = current
            else:
                native = target.request('POST', '/api/routines', content)['routine']
            saved['routines'][routine['sourceId']] = dict(id=native['id'], hash=digest(content), expected=content)
            write_private(receipt_file, receipt)
        print(json.dumps(dict(bot=bot['name'], entries=bot['entries'], skills=len(saved['skills']), routines=len(saved['routines']))), flush=True)
    receipt['bundleHash'] = digest(bundle)
    receipt['sourceBots'] = len(bundle['bots'])
    write_private(receipt_file, receipt)
    return receipt


def import_document(bot: dict, attachments: dict) -> dict:
    document = copy.deepcopy(bot['document'])
    for task in document['bots'][0]['tasks']:
        for message in task['messages']:
            if message['role'] == 'user' and attachments.get(message['id']):
                tags = ['<attached-' + item['kind'] + ' path="' + html.escape(item['path'], quote=True) + '" name="' + html.escape(item['name'], quote=True) + '" />'
                        for item in attachments[message['id']]]
                message['text'] += '\n\n' + '\n\n'.join(tags)
    return document


def verify(bundle_file: Path, url: str, receipt_file: Path, complete: bool = False) -> dict:
    bundle = json.loads(bundle_file.read_text())
    receipt = json.loads(receipt_file.read_text())
    target = LocalMaus(url)
    if receipt['target'] != target.url:
        raise ValueError('The receipt belongs to a different target')
    state = target.request('GET', '/api/bots?messages=0')
    bots = {bot['id']: bot for bot in state['bots']}
    routines = {routine['id']: routine for routine in target.request('GET', '/api/routines')['routines']}
    checks = []
    for source in bundle['bots']:
        saved = receipt['bots'].get(source['key'])
        if not saved or not saved.get('botId'):
            continue
        bot = bots.get(saved['botId'])
        if not bot:
            raise ValueError('An imported bot is absent: ' + source['name'])
        messages = target.messages(saved['threads'][0])
        expected = import_document(source, saved.get('attachments', {}))['bots'][0]
        wanted = expected['tasks'][0]['messages']
        if len(messages) != len(wanted) or any((a['role'], a.get('text', ''), a['at']) != (b['role'], b['text'], b['at']) for a, b in zip(messages, wanted, strict=True)):
            raise ValueError('Target chat differs: ' + source['name'])
        by_source = dict(zip([message['id'] for message in wanted], messages, strict=True))
        for message_id, reactions in source.get('reactions', {}).items():
            mapped = [dict(emoji=item['emoji'], by=bot['id'] if item['by'] == 'self' else 'user') for item in reactions]
            if by_source[message_id].get('reactions') != mapped:
                raise ValueError('Target reactions differ: ' + source['name'])
        skills = {item['name']: item for item in target.request('GET', f'/api/bots/{bot["id"]}/skills')['skills']}
        for skill in source['skills']:
            current = skills.get(skill['name'])
            if not current or current['enabled'] != skill['enabled'] or target.request('GET', f'/api/bots/{bot["id"]}/skills/{skill["name"]}')['text'] != skill['text']:
                raise ValueError('Target skill differs: ' + source['name'] + '/' + skill['name'])
        for routine in source['routines']:
            record = saved['routines'].get(routine['sourceId'])
            if not record:
                raise ValueError('Missing routine receipt: ' + routine['name'])
            if routine['replacement']:
                if record.get('replacement') != routine['replacement']:
                    raise ValueError('Routine replacement differs: ' + routine['name'])
                continue
            current = routines.get(record['id'])
            if not current or any(current.get(key) != value for key, value in record['expected'].items()):
                raise ValueError('Target routine differs: ' + routine['name'])
            if current['prompt'] != routine['prompt'] or current['schedule'] != routine['schedule'] or current['enabled'] != routine['enabled']:
                raise ValueError('Target routine does not match source: ' + routine['name'])
        files = 0
        for file in source['files']:
            if not file['recovered']:
                raise ValueError('Source file is missing: ' + file['name'])
            data = Path(file['path']).read_bytes()
            if file.get('sha256') and hashlib.sha256(data).hexdigest() != file['sha256']:
                raise ValueError('Source file checksum differs: ' + file['name'])
            message = by_source[file['messageId']]
            uploaded = saved.get('attachments', {}).get(file['messageId'])
            if uploaded:
                if message.get('attachments') != uploaded:
                    raise ValueError('Target attachment differs: ' + file['name'])
                route = f'/api/threads/{saved["threads"][0]}/messages/{message["id"]}/file'
                if target.download(route, dict(path=uploaded[0]['path'])) != data:
                    raise ValueError('Target file bytes differ: ' + file['name'])
            elif message['role'] == 'bot':
                route = f'/api/threads/{saved["threads"][0]}/messages/{message["id"]}/file'
                if target.download(route, dict(path=Path(file['path']).as_uri())) != data:
                    raise ValueError('Archived file bytes differ: ' + file['name'])
            else:
                raise ValueError('A user file needs native upload support: ' + file['name'])
            files += 1
        memory = target.request('GET', f'/api/bots/{bot["id"]}/memory')['text']
        if saved.get('preserved'):
            if any(bot.get(key) != value for key, value in saved['preserved'].items()) or memory != saved['preservedMemory']:
                raise ValueError('A bound bot profile or memory changed: ' + source['name'])
        else:
            for key in ['name', 'description', 'soul', 'color', 'hidden', 'mascotBody']:
                if key in expected and bot.get(key, False if key == 'hidden' else None) != expected[key]:
                    raise ValueError('Target profile differs: ' + source['name'] + '/' + key)
            if any(bot.get(key) != value for key, value in saved.get('profile', {}).items()):
                raise ValueError('Target runtime differs: ' + source['name'])
            if memory != expected['memory']['file']:
                raise ValueError('Target memory seed differs: ' + source['name'])
        checks.append(dict(name=source['name'], entries=len(messages), skills=len(source['skills']), enabledSkills=sum(s['enabled'] for s in source['skills']),
                           routines=sum(not r['replacement'] for r in source['routines']), files=files,
                           reactions=sum(len(r) for r in source.get('reactions', {}).values()), hidden=bot.get('hidden', False), memoryBytes=len(memory.encode())))
        print(json.dumps(dict(verified=source['name'], entries=len(messages), files=files)), flush=True)
    if complete and len(checks) != len(bundle['bots']):
        raise ValueError('The full roster is not imported')
    if bundle['missingFiles']:
        raise ValueError('The bundle has missing files')
    proof = dict(ok=True, complete=len(checks) == len(bundle['bots']), bots=checks, verifiedBots=len(checks), sourceBots=len(bundle['bots']), bundleHash=digest(bundle),
                 memoryPolicy='light', contentGaps=dict(files=bundle['missingFiles']))
    write_private(receipt_file.with_name(receipt_file.stem + '-proof.json'), proof)
    return proof
