"""Use the app's loaded read methods. Never submit a prompt or edit a source bot."""
from __future__ import annotations
import json
from pathlib import Path
import subprocess
from typing import Any
from .archive import source_id, update_manifest, write_private


def parse_cli_result(output: str) -> Any:
    if '### Error' in output:
        raise RuntimeError(output.split('### Error', 1)[1].split('###', 1)[0].strip())
    if '### Result\n' not in output:
        raise RuntimeError('Playwright did not return a JSON result')
    text = output.split('### Result\n', 1)[1]
    return json.JSONDecoder().raw_decode(text.lstrip())[0]


class Browser:
    def __init__(self, session: str):
        self.session = session
        self.bridge = Path(__file__).with_name('capture.js').read_text()

    def attach_reader(self) -> None:
        result = subprocess.run(['playwright-cli', f'-s={self.session}', 'run-code', '--filename',
            str(Path(__file__).with_name('attach_reader.js'))], capture_output=True, text=True, timeout=30)
        if result.returncode:
            raise RuntimeError('Could not attach the read client')
        parse_cli_result(result.stdout)

    def read(self, operation: str, **args: Any) -> Any:
        code = f'async () => await {self.bridge}({json.dumps(dict(operation=operation, **args))})'
        result = subprocess.run(['playwright-cli', f'-s={self.session}', 'eval', code],
                                capture_output=True, text=True, timeout=120)
        if result.returncode:
            raise RuntimeError(result.stderr.strip() or 'Playwright capture failed')
        return parse_cli_result(result.stdout)


def capture(session: str, output: Path, only: list[str] | None = None) -> dict:
    try:
        return _capture(session, output, only)
    finally:
        if (output / 'roster.json').exists():
            update_manifest(output)


def _capture(session: str, output: Path, only: list[str] | None) -> dict:
    browser = Browser(session)
    roster = browser.read('roster')
    write_private(output / 'roster.json', roster)
    browser.attach_reader()
    previous = output / 'capture-summary.json'
    prior = json.loads(previous.read_text()).get('bots', []) if previous.exists() else []
    valid_ids = {bot['id'] for bot in roster['agents']}
    summary = [bot for bot in prior if bot['id'] in valid_ids]
    for agent in roster['agents']:
        bot_id = agent['id']
        if only and bot_id not in only and agent['name'] not in only:
            continue
        source_id(bot_id)
        setup = browser.read('setup', id=bot_id)
        write_private(output / f'{bot_id}-setup.json', setup)
        # Keep prior pages if the browser or the export process was interrupted.
        dest = output / f'{bot_id}-transcript.json'
        old = json.loads(dest.read_text()) if dest.exists() else {}
        entries = {entry['id']: entry for entry in old.get('entries', [])}
        write_private(dest, dict(complete=False, entries=list(entries.values())))
        failure = None
        complete = False
        before_seq = None
        for page in range(10000):
            state = browser.read('page', id=bot_id, **({} if before_seq is None else {'beforeSeq': before_seq}))
            batch = state['entries']
            if not batch:
                complete = True
                break
            for entry in batch:
                entries[entry['id']] = entry
            next_seq = min(entry['seq'] for entry in batch)
            if before_seq is not None and next_seq >= before_seq:
                failure = 'History pagination made no progress'
                break
            before_seq = next_seq
            write_private(dest, dict(complete=False, pages=page + 1, entries=list(entries.values())))
        write_private(dest, dict(complete=complete, pages=page + 1, failure=failure, entries=list(entries.values())))
        item = dict(id=bot_id, name=agent['name'], entries=len(entries), complete=complete, pages=page + 1,
                    skills=len(setup['skills'].get('value', [])),
                    routines=len(setup['routines'].get('value', [])))
        summary = [bot for bot in summary if bot['id'] != bot_id] + [item]
        write_private(output / 'capture-summary.json', dict(capturedAt=roster['capturedAt'], bots=summary))
        update_manifest(output)
        print(json.dumps(item), flush=True)
    return dict(capturedAt=roster['capturedAt'], bots=summary)
