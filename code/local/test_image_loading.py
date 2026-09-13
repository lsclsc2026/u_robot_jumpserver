import copy
import importlib.util
import json
from pathlib import Path
import tarfile
import unittest
from unittest.mock import patch

BASE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('installer', BASE / 'install-jumpserver-laptop.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
ITEM = json.loads((BASE / 'images/manifest.json').read_text())[0]
with tarfile.open(BASE / 'images' / ITEM['file']) as archive:
    manifest = json.load(archive.extractfile('manifest.json'))[0]
    EXPECTED = json.load(archive.extractfile(manifest['Config']))
INSPECT = {'Id': 'sha256:' + '1' * 64, 'Architecture': EXPECTED['architecture'],
           'Os': EXPECTED['os'], 'Config': EXPECTED['config'],
           'RootFS': {'Type': EXPECTED['rootfs']['type'], 'Layers': EXPECTED['rootfs']['diff_ids']}}

class ImageLoadingTests(unittest.TestCase):
    def test_accepts_manifest_id_when_verified_config_and_layers_match(self):
        self.assertTrue(callable(getattr(m, 'verify_loaded_image', None)), 'Missing store-independent validation')
        m.verify_loaded_image(INSPECT, EXPECTED)

    def test_rejects_different_filesystem_layer(self):
        self.assertTrue(callable(getattr(m, 'verify_loaded_image', None)))
        changed = copy.deepcopy(INSPECT)
        changed['RootFS']['Layers'][0] = 'sha256:' + '0' * 64
        with self.assertRaisesRegex(RuntimeError, 'filesystem'):
            m.verify_loaded_image(changed, EXPECTED)

    def test_rejects_modified_entrypoint(self):
        self.assertTrue(callable(getattr(m, 'verify_loaded_image', None)))
        changed = copy.deepcopy(INSPECT)
        changed['Config']['Entrypoint'] = ['/bin/false']
        with self.assertRaisesRegex(RuntimeError, 'configuration'):
            m.verify_loaded_image(changed, EXPECTED)

    def test_reuses_loaded_tag_without_config_digest_lookup(self):
        self.assertTrue(callable(getattr(m, 'load_offline_image', None)), 'Missing tag-based load path')
        # A real daemon is inaccessible without the user's sudo. Emulate only the
        # Docker boundary; the real archive parser and validation must run.
        with patch.object(m, 'STAGING', BASE), patch.object(m, 'inspect_image', return_value=INSPECT) as inspect, patch.object(m, 'run', return_value='') as run:
            result = m.load_offline_image(ITEM)
        self.assertEqual(result, ITEM['ref'])
        self.assertNotIn(ITEM['image_id'], [c.args[0] for c in inspect.call_args_list])
        self.assertEqual(run.call_args_list[0].args[0], ['docker', 'tag', 'jumpserver/core:i-was-a-digest', ITEM['ref']])
        self.assertEqual(run.call_count, 1)

if __name__ == '__main__':
    unittest.main(verbosity=2)
