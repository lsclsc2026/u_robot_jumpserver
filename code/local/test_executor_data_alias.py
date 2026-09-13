import importlib.util
from pathlib import Path
import tempfile
import unittest
spec=importlib.util.spec_from_file_location('helper',Path(__file__).with_name('add-jumpserver-executor.py'))
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
class DataAliasTests(unittest.TestCase):
 def test_creates_alias_and_preserves_task_file(self):
  self.assertTrue(callable(getattr(m,'ensure_data_alias',None)))
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);data=root/'data';data.mkdir();(data/'task').write_text('sentinel');alias=root/'alias'
   m.ensure_data_alias(data,alias);m.ensure_data_alias(data,alias)
   self.assertEqual((alias/'task').read_text(),'sentinel')
 def test_refuses_existing_directory_without_modifying_it(self):
  self.assertTrue(callable(getattr(m,'ensure_data_alias',None)))
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);data=root/'data';data.mkdir();alias=root/'alias';alias.mkdir();(alias/'keep').write_text('keep')
   with self.assertRaises(RuntimeError):m.ensure_data_alias(data,alias)
   self.assertEqual((alias/'keep').read_text(),'keep')
 def test_refuses_wrong_link(self):
  self.assertTrue(callable(getattr(m,'ensure_data_alias',None)))
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);data=root/'data';data.mkdir();other=root/'other';other.mkdir();alias=root/'alias';alias.symlink_to(other)
   with self.assertRaises(RuntimeError):m.ensure_data_alias(data,alias)
   self.assertEqual(alias.resolve(),other)
if __name__=='__main__':unittest.main(verbosity=2)
