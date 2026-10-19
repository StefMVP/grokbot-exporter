import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import zipfile
from grokbot_exporter.cloud import extract
from grokbot_exporter.adapters.openmaus.apply import LocalMaus
from grokbot_exporter.capture import parse_cli_result


class CloudTests(unittest.TestCase):
    def test_corrupt_file_and_path_escape_fail_before_extract(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for name, data, expected in [('../escape', b'x', b'x'), ('normal.txt', b'corrupt', b'expected')]:
                archive = root / 'cloud.zip'
                with zipfile.ZipFile(archive, 'w') as bundle:
                    bundle.writestr(name, data)
                    bundle.writestr('inventory.json', json.dumps(dict(files=[dict(path=name, bytes=len(expected), sha256=hashlib.sha256(expected).hexdigest())], errors=[])))
                with self.assertRaises(ValueError):
                    extract(archive, root / 'output')
            self.assertFalse((root / 'escape').exists())

    def test_existing_symlink_cannot_create_an_outside_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            output, outside = root / 'output', root / 'outside'
            output.mkdir(); outside.mkdir()
            (output / 'link').symlink_to(outside, target_is_directory=True)
            archive = root / 'cloud.zip'
            with zipfile.ZipFile(archive, 'w') as bundle:
                bundle.writestr('link/new/file.txt', 'source')
                bundle.writestr('inventory.json', json.dumps(dict(files=[dict(path='link/new/file.txt', bytes=6,
                    sha256=hashlib.sha256(b'source').hexdigest())], errors=[])))
            with self.assertRaises(ValueError):
                extract(archive, output)
            self.assertFalse((outside / 'new').exists())

    def test_importer_refuses_remote_and_url_credentials(self):
        for url in ['http://example.com:8799', 'http://key@localhost:8799', 'http://localhost:8799/?token=key']:
            with self.assertRaises(ValueError):
                LocalMaus(url)

    def test_browser_result_does_not_silently_accept_partial_or_error_output(self):
        self.assertEqual(parse_cli_result('### Result\n{"ok":true}\n### Ran code\n...'), {'ok': True})
        for output in ['no result', '### Error\nDisconnected']:
            with self.assertRaises(RuntimeError):
                parse_cli_result(output)
