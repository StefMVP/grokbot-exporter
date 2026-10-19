import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from grokbot_exporter.archive import digest
from grokbot_exporter.adapters.openmaus.cutover import cutover


class CutoverTests(unittest.TestCase):
    def test_incomplete_proof_cannot_change_source_schedules(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'bundle.json').write_text(json.dumps(dict(bots=[])))
            (root / 'proof.json').write_text(json.dumps(dict(ok=True, complete=False)))
            with patch('grokbot_exporter.adapters.openmaus.cutover.Browser') as browser:
                with self.assertRaisesRegex(ValueError, 'complete matching'):
                    cutover('test', root / 'bundle.json', root / 'proof.json', root / 'receipt.json')
                browser.assert_not_called()

    def test_webhook_backup_is_never_paused(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            routine = dict(sourceId='hook', name='Backup', prompt='Read current events', enabled=True, sourceTrigger=dict(type='webhook'))
            bundle = dict(bots=[dict(key='b', name='Reader', routines=[routine])])
            (root / 'bundle.json').write_text(json.dumps(bundle))
            (root / 'proof.json').write_text(json.dumps(dict(ok=True, complete=True, bundleHash=digest(bundle))))
            with patch('grokbot_exporter.adapters.openmaus.cutover.Browser') as browser, patch('grokbot_exporter.adapters.openmaus.cutover.subprocess.run') as run:
                browser.return_value.read.side_effect = [dict(agents=[]), dict(ok=True, value=[])]
                result = cutover('test', root / 'bundle.json', root / 'proof.json', root / 'receipt.json')
                run.assert_not_called()
            self.assertEqual(result['changed'], 0)
            self.assertEqual(result['sourceWebhookBackups'], 'preserved')
