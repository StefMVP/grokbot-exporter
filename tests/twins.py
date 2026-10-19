"""A local OpenMaus protocol twin, with state saved across server restarts."""
import base64
from contextlib import AbstractContextManager
import copy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
from urllib.parse import parse_qs, unquote, urlparse
import uuid


class OpenMausTwin(AbstractContextManager):
    def __init__(self, root: Path):
        self.root = root
        self.file = root / 'target.json'
        self.file.write_text(json.dumps(dict(bots={}, memories={}, threads={}, skills={}, routines=[], files={}, calls=[])))
        self.port = 0

    def read(self):
        return json.loads(self.file.read_text())

    def write(self, value):
        self.file.write_text(json.dumps(value))

    def __enter__(self):
        twin = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def handle_request(self):
                parsed = urlparse(self.path)
                route = parsed.path
                params = parse_qs(parsed.query)
                parts = route.strip('/').split('/')
                raw = self.rfile.read(int(self.headers.get('Content-Length', 0)))
                body = json.loads(raw) if raw and self.headers.get('Content-Type') == 'application/json' else None
                state = twin.read()
                key = self.command + ' ' + route
                state['calls'].append(key)
                try:
                    if state.get('failOnce') == key:
                        del state['failOnce']
                        raise ValueError('503: Twin connection interrupted')
                    result = self.dispatch(state, route, parts, params, body, raw)
                    status = 200
                except ValueError as error:
                    status = int(str(error).split(':', 1)[0])
                    result = dict(error=str(error))
                except Exception as error:
                    status, result = 500, dict(error=repr(error))
                twin.write(state)
                self.send_response(status)
                self.send_header('Content-Type', 'application/octet-stream' if isinstance(result, bytes) else 'application/json')
                self.end_headers()
                self.wfile.write(result if isinstance(result, bytes) else json.dumps(result).encode())

            do_GET = do_POST = do_PATCH = handle_request

            def dispatch(self, state, route, parts, params, body, raw):
                if route == '/api/bots' and self.command == 'GET':
                    return dict(bots=list(state['bots'].values()))
                if route == '/api/teams/export':
                    return dict(format='openmaus.backup', version=1, bots=list(state['bots'].values()))
                if route in ['/api/attachments', '/api/files']:
                    location = '/twin/attachments/' + params['uploadId'][0]
                    state['files'][location] = base64.b64encode(raw).decode()
                    return dict(path=location, mime=self.headers['Content-Type'])
                if route == '/api/migrations/bots':
                    document = body['document']
                    if document['format'] != 'openmaus.backup' or document['version'] != 1:
                        raise ValueError('422: Unsupported native document')
                    bot_id = body.get('bindBotId') or 'bot-' + body['key']
                    source = document['bots'][0]
                    thread_id = 'thread-' + body['key']
                    messages = []
                    for message in source['tasks'][0]['messages']:
                        item = copy.deepcopy(message)
                        item['id'] = str(uuid.uuid5(uuid.NAMESPACE_URL, bot_id + ':' + message['id']))
                        if message['id'] in body['reactions']:
                            item['reactions'] = [dict(emoji=value['emoji'], by=bot_id if value['by'] == 'self' else 'user')
                                                 for value in body['reactions'][message['id']]]
                        if message['id'] in body['attachments']:
                            item['attachments'] = body['attachments'][message['id']]
                            if any(value['path'] not in state['files'] for value in item['attachments']):
                                raise ValueError('422: Attachment was not uploaded')
                        messages.append(item)
                    current = state['threads'].get(thread_id, [])
                    if current != messages[:len(current)]:
                        raise ValueError('409: Keep local replies or changed messages')
                    if bot_id not in state['bots']:
                        state['bots'][bot_id] = dict(source, id=bot_id, threadId=thread_id)
                        state['memories'][bot_id] = source['memory']['file']
                    state['threads'][thread_id] = messages
                    return dict(botId=bot_id, threadIds=[thread_id])
                if len(parts) >= 3 and parts[:2] == ['api', 'bots']:
                    bot_id = parts[2]
                    if len(parts) == 3 and self.command == 'PATCH':
                        state['bots'][bot_id].update(body)
                        return state['bots'][bot_id]
                    if parts[3:] == ['memory']:
                        return dict(text=state['memories'][bot_id])
                    if parts[3:] == ['skill-template']:
                        state['skills'].setdefault(bot_id, {})[body['name']] = body
                        return dict(skill=body)
                    if len(parts) >= 4 and parts[3] == 'skills':
                        skills = state['skills'].get(bot_id, {})
                        return skills[parts[4]] if len(parts) == 5 else dict(skills=list(skills.values()))
                if route == '/api/routines':
                    if self.command == 'POST':
                        routine = dict(body, id='routine-' + str(len(state['routines'])))
                        state['routines'].append(routine)
                        return dict(routine=routine)
                    return dict(routines=state['routines'])
                if parts[:2] == ['api', 'threads']:
                    messages = state['threads'][parts[2]]
                    if parts[-1] == 'file':
                        location = body['path']
                        if location in state['files']:
                            return base64.b64decode(state['files'][location])
                        local = Path(unquote(urlparse(location).path)).resolve()
                        if not local.is_relative_to(twin.root):
                            raise ValueError('403: File is outside the fixture')
                        return local.read_bytes()
                    end = next((i for i, item in enumerate(messages) if item['id'] == params.get('before', [None])[0]), len(messages))
                    return dict(messages=messages[max(0, end - 2):end], hasMore=end > 2)
                raise ValueError('404: Unsupported twin route')

        self.server = ThreadingHTTPServer(('127.0.0.1', self.port), Handler)
        self.port = self.server.server_port
        self.url = f'http://127.0.0.1:{self.port}'
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs=dict(poll_interval=0.01), daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def restart(self):
        self.__exit__()
        self.__enter__()
