import base64
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from grokbot_exporter.recover import recover


class RecoverTests(unittest.TestCase):
    def test_original_image_fallback_after_file_reader_returns_nothing(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'roster.json').write_text(json.dumps(dict(agents=[dict(id='b', name='Reader')])))
            (root / 'b-transcript.json').write_text(json.dumps(dict(entries=[dict(kind='user-attachment', file_path='/home/box/agent-data/agents/b/attachments/a.png')])))
            data = b'original image fixture'
            with patch('grokbot_exporter.recover.Browser') as cls:
                cls.return_value.read.side_effect = [None, dict(src='data:image/png;base64,' + base64.b64encode(data).decode())]
                proof = recover('fixture', root)
            self.assertTrue(proof['ok'])
            self.assertEqual((root / 'cloud/agents/b/attachments/a.png').read_bytes(), data)
            self.assertEqual(proof['recovered'][0]['reader'], 'original-image')
