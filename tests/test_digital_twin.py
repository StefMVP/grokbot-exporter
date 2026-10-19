"""Run the real CLI and JavaScript readers against local protocol twins."""
from contextlib import closing
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from grokbot_exporter.archive import verify_export
from grokbot_exporter.cloud_export import export_cloud
from twins import OpenMausTwin

FIXTURES = Path(__file__).with_name('fixtures')


class DigitalTwinTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.export = self.root / 'export'
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        executable = self.bin / 'playwright-cli'
        executable.write_text(Path(__file__).with_name('twin_playwright.js').read_text())
        executable.chmod(0o700)
        self.state_file = self.root / 'source.json'
        load = lambda name: json.loads((FIXTURES / name).read_text())
        self.state = dict(roster=load('roster.json'), setup=dict(reader=load('reader-setup.json'),
                          writer=dict(skills=dict(ok=True, value=[]), routines=dict(ok=True, value=[]))),
                          entries=dict(reader=load('reader-transcript.json')['entries'], writer=[]),
                          files={'/home/box/agent-data/agents/reader/attachments/chart.png':
                                 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j3ZkAAAAASUVORK5CYII='})
        self.write_source()
        self.env = dict(os.environ, GROKBOT_TWIN_STATE=str(self.state_file), PATH=str(self.bin) + os.pathsep + os.environ['PATH'])
        if not os.environ.get('GROKBOT_TEST_INSTALLED'):
            self.env['PYTHONPATH'] = str(Path(__file__).resolve().parents[1])
        else:
            self.env.pop('PYTHONPATH', None)
        self.bundle = self.root / 'bundle.json'
        self.receipt = self.root / 'receipt.json'
        self.config = self.root / 'config.json'
        self.config.write_text(json.dumps(dict(modelSelection=dict(instanceId='twin', model='test-model'))))

    def write_source(self):
        self.state_file.write_text(json.dumps(self.state))

    def cli(self, *args, success=True):
        result = subprocess.run([sys.executable, '-m', 'grokbot_exporter.cli', *map(str, args)],
                                cwd=self.root, env=self.env, capture_output=True, text=True, timeout=30)
        if success:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout)
        return result

    def capture(self):
        self.cli('capture', '--session', 'digital-twin', '--output', self.export)

    def prepare(self):
        self.capture()
        workspace = self.root / 'cloud-source/workspace'
        workspace.mkdir(parents=True)
        (workspace / 'notes.md').write_text('# Project\n\n- Read notes\n- Write update\n')
        (workspace / '.env').write_text('PRIVATE_FIXTURE=value')
        (workspace / 'node_modules').mkdir()
        (workspace / 'node_modules/ignored.txt').write_text('excluded')
        with closing(sqlite3.connect(workspace / 'notes.db')) as db, db:
            db.execute('CREATE TABLE notes (text TEXT)')
            db.execute('INSERT INTO notes VALUES (?)', ('Read current notes',))
        remote = self.root / 'remote'
        export_cloud(remote, self.root / 'cloud-source/agents', workspace, self.root / 'cloud-source/reference')
        self.state = json.loads(self.state_file.read_text())
        self.state['remoteRoot'] = str(remote)
        self.write_source()
        self.cli('pull-cloud', '--session', 'digital-twin', '--bot-id', 'reader', '--output', self.export)
        self.cli('recover-files', '--session', 'digital-twin', '--source', self.export)
        self.cli('check', '--source', self.export)
        self.cli('openmaus', 'build', '--source', self.export, '--output', self.bundle, '--host-root', self.root)

    def apply(self, target, success=True):
        return self.cli('openmaus', 'apply', '--bundle', self.bundle, '--config', self.config,
                        '--url', target.url, '--receipt', self.receipt, success=success)

    def verify(self, target, success=True):
        return self.cli('openmaus', 'verify', '--bundle', self.bundle, '--url', target.url,
                        '--receipt', self.receipt, '--complete', success=success)

    def test_full_flow_retry_restart_and_cutover(self):
        self.prepare()
        self.assertFalse((self.export / 'cloud/workspace/.env').exists())
        self.assertFalse((self.export / 'cloud/workspace/node_modules').exists())
        self.assertEqual(json.loads((self.export / 'cloud/workspace/notes.db.json').read_text()),
                         {'notes': [{'text': 'Read current notes'}]})
        with OpenMausTwin(self.root) as target:
            self.apply(target)
            self.verify(target)
            before = target.read()
            target.restart()
            self.apply(target)
            self.verify(target)
            after = target.read()
            for name in ['bots', 'threads', 'skills', 'routines', 'files', 'memories']:
                self.assertEqual(before[name], after[name], name)
            self.assertEqual(len(after['bots']), 2)
            self.assertEqual(len(after['threads']['thread-reader']), 5)
            self.assertFalse(after['skills']['bot-reader']['vendor-wrapper']['enabled'])
            proof = self.root / 'receipt-proof.json'
            cutover = self.root / 'cutover.json'
            args = ['openmaus', 'cutover', '--session', 'digital-twin', '--bundle', self.bundle,
                    '--proof', proof, '--receipt', cutover]
            self.cli(*args)
            paused = json.loads(self.state_file.read_text())
            self.assertFalse(paused['setup']['reader']['routines']['value'][0]['isEnabled'])
            self.cli(*args, '--restore')
            restored = json.loads(self.state_file.read_text())
            self.assertTrue(restored['setup']['reader']['routines']['value'][0]['isEnabled'])
            self.assertEqual(restored['calls'].count('setEnabled'), 2)

    def test_interrupted_capture_resumes_and_export_can_move(self):
        self.state['failBeforeSeq'] = 4
        self.write_source()
        self.cli('capture', '--session', 'digital-twin', '--output', self.export, success=False)
        self.assertFalse(verify_export(self.export)['complete'])
        self.capture()
        self.assertTrue(verify_export(self.export)['complete'])
        moved = self.root / 'moved'
        shutil.move(self.export, moved)
        self.cli('check', '--source', moved)
        entries = json.loads((moved / 'reader-transcript.json').read_text())['entries']
        self.assertEqual(sorted(entries, key=lambda item: item['seq']), self.state['entries']['reader'])
        with (moved / 'reader-transcript.json').open('a') as stream:
            stream.write(' ')
        result = self.cli('check', '--source', moved, success=False)
        self.assertIn('checksum', result.stderr)

    def test_pagination_that_stops_moving_is_incomplete(self):
        self.state['stall'] = True
        self.write_source()
        self.capture()
        self.assertFalse(verify_export(self.export)['complete'])
        self.cli('check', '--source', self.export, success=False)

    def test_interrupted_apply_resumes_and_local_reply_is_kept(self):
        self.prepare()
        with OpenMausTwin(self.root) as target:
            state = target.read()
            state['failOnce'] = 'POST /api/bots/bot-reader/skill-template'
            target.write(state)
            self.apply(target, success=False)
            self.apply(target)
            self.verify(target)
            state = target.read()
            state['threads']['thread-reader'].append(dict(id='local', role='user', text='Keep this new reply', at=999))
            target.write(state)
            result = self.apply(target, success=False)
            self.assertIn('409', result.stderr)
            self.assertEqual(target.read()['threads']['thread-reader'][-1]['text'], 'Keep this new reply')

    def test_binding_keeps_existing_profile_and_memory(self):
        self.prepare()
        self.config.write_text(json.dumps(dict(modelSelection=dict(instanceId='twin', model='test-model'), bindings=dict(Reader='existing'))))
        with OpenMausTwin(self.root) as target:
            state = target.read()
            profile = dict(id='existing', name='My existing bot', description='Current role', soul='Current instructions',
                           cwd=str(self.root), threadId='active-local', modelSelection=dict(instanceId='local', model='existing'))
            state['bots']['existing'] = profile
            state['memories']['existing'] = 'Keep current memory'
            target.write(state)
            self.apply(target)
            self.verify(target)
            self.assertEqual(target.read()['bots']['existing'], profile)
            self.assertEqual(target.read()['memories']['existing'], 'Keep current memory')

    def test_target_corruption_fails_verification(self):
        self.prepare()
        with OpenMausTwin(self.root) as target:
            self.apply(target)
            state = target.read()
            state['threads']['thread-reader'][0]['text'] = 'Changed text'
            target.write(state)
            self.assertIn('Target chat differs', self.verify(target, success=False).stderr)
