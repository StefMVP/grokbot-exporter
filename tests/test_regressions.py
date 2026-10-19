import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from grokbot_exporter.adapters.openmaus.apply import apply
from grokbot_exporter.archive import source_file
from grokbot_exporter.cloud import extract


class ReleaseRegressionTests(unittest.TestCase):
    def test_source_paths_stay_inside_the_archive(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for value in ['/workspace//tmp/outside', '/workspace/../outside']:
                with self.subTest(value=value):
                    self.assertIsNone(source_file(value, root))
            (root / 'workspace').mkdir()
            (root / 'workspace/link').symlink_to(root.parent, target_is_directory=True)
            self.assertIsNone(source_file('/workspace/link/outside', root))

    def test_inventory_must_match_archive_members(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            record = dict(path='listed.txt', bytes=1, sha256=hashlib.sha256(b'x').hexdigest())
            for members in [{'extra.txt': b'x'}, {'listed.txt': b'x', 'extra.txt': b'x'}, {}]:
                with self.subTest(members=list(members)):
                    archive = root / 'cloud.zip'
                    with zipfile.ZipFile(archive, 'w') as bundle:
                        for name, data in members.items():
                            bundle.writestr(name, data)
                        bundle.writestr('inventory.json', json.dumps(dict(files=[record], errors=[])))
                    with self.assertRaisesRegex(ValueError, 'inventory'):
                        extract(archive, root / 'output')

    def test_unmapped_webhook_fails_before_contacting_target(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            bot = dict(key='reader', name='Reader', files=[], routines=[dict(name='Incoming', replacement=None, schedule=None)])
            (root / 'bundle.json').write_text(json.dumps(dict(format='grokbot.openmaus', version=1, namespace='grokbot', bots=[bot])))
            (root / 'config.json').write_text(json.dumps(dict(modelSelection=dict(instanceId='local', model='example'))))
            with patch('grokbot_exporter.adapters.openmaus.apply.LocalMaus') as target:
                target.return_value.request.side_effect = AssertionError('Target contacted before validation')
                with self.assertRaisesRegex(ValueError, 'webhook'):
                    apply(root / 'bundle.json', root / 'config.json', 'http://127.0.0.1:1', root / 'receipt.json')
                target.assert_not_called()
