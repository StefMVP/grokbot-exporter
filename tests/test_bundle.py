import json
from pathlib import Path
import tempfile
import unittest
from grokbot_exporter.adapters.openmaus.bundle import build, schedule
from grokbot_exporter.archive import source_file, update_manifest


class BundleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.agent = dict(id='bot-1', name='Chief', description='Manage the current team.', createdAt=100)
        (self.root / 'roster.json').write_text(json.dumps(dict(agents=[self.agent])))
        self.history = dict(complete=True, entries=[dict(id='q', seq=1, kind='message', content='A long old project detail', timestampMs=101),
            dict(id='a', seq=2, kind='send-message', timestampMs=102, message=dict(type='text', content='A past answer'), reactions=[dict(emoji='👍', by='bot-1')])])
        (self.root / 'bot-1-transcript.json').write_text(json.dumps(self.history))
        self.setup = dict(skills=dict(ok=True, value=[]), routines=dict(ok=True, value=[dict(id='r', name='Daily review', prompt='Read the current queue.', isEnabled=True,
            trigger=dict(type='schedule', schedule='CRON_TZ=America/Chicago 0 8 * * *'))]))
        (self.root / 'bot-1-setup.json').write_text(json.dumps(self.setup))
        update_manifest(self.root)

    def test_chief_is_not_replaced_unless_policy_names_it(self):
        build(self.root, self.root / 'bundle.json', self.root)
        bot = json.loads((self.root / 'bundle.json').read_text())['bots'][0]
        self.assertIsNone(bot['routines'][0]['replacement'])
        build(self.root, self.root / 'bundle.json', self.root, dict(routineReplacements=[dict(bot='Chief', replacement='current-event-route')]))
        bot = json.loads((self.root / 'bundle.json').read_text())['bots'][0]
        self.assertEqual(bot['routines'][0]['replacement'], 'current-event-route')

    def test_history_and_reactions_survive_without_filling_memory(self):
        build(self.root, self.root / 'bundle.json', self.root)
        bot = json.loads((self.root / 'bundle.json').read_text())['bots'][0]
        target = bot['document']['bots'][0]
        self.assertEqual([m['text'] for m in target['tasks'][0]['messages']], ['A long old project detail', 'A past answer'])
        self.assertEqual(bot['reactions'], {'a': [dict(emoji='👍', by='self')]})
        self.assertNotIn('A long old project detail', target['memory']['file'])
        self.assertLess(len(target['memory']['file']), 600)
        self.assertEqual(target['memory']['topics'], [])

    def test_role_and_host_policy_are_explicit(self):
        build(self.root, self.root / 'bundle.json', self.root)
        target = json.loads((self.root / 'bundle.json').read_text())['bots'][0]['document']['bots'][0]
        self.assertFalse(target['chiefOfStaff'])
        self.assertNotIn('slack-local', target['soul'])
        self.assertNotIn('agent_config', target['soul'])
        build(self.root, self.root / 'bundle.json', self.root, dict(chiefOfStaff=['bot-1'], runtimeNotes=['Use my current host tools.']))
        target = json.loads((self.root / 'bundle.json').read_text())['bots'][0]['document']['bots'][0]
        self.assertTrue(target['chiefOfStaff'])
        self.assertIn('Use my current host tools.', target['soul'])

    def test_incomplete_history_fails_before_build(self):
        self.history['complete'] = False
        (self.root / 'bot-1-transcript.json').write_text(json.dumps(self.history))
        update_manifest(self.root)
        with self.assertRaisesRegex(ValueError, 'Incomplete history'):
            build(self.root, self.root / 'bundle.json', self.root)

    def test_archive_paths_cannot_escape_and_dst_zone_is_kept(self):
        self.assertIsNone(source_file('/workspace/../../etc/passwd', self.root))
        self.assertEqual(schedule(dict(type='schedule', schedule='CRON_TZ=America/Chicago 30 5 * * *')), dict(type='cron', expression='30 5 * * *', timeZone='America/Chicago'))
        with self.assertRaises(ValueError):
            schedule(dict(type='schedule', schedule='0 8 * * *'))
