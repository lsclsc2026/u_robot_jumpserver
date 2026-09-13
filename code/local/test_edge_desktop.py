import unittest
import tempfile
from pathlib import Path
import setup_edge_desktop as m
class ConfigTests(unittest.TestCase):
 def test_preserve_xorg_and_idempotent(self):
  s='[Globals]\nport=3389\n[Other]\nport=-1\n[Xorg]\nparam=Xorg\nparam=-config\nparam=xrdp/xorg.conf\n'
  out=m.update(s,'Globals',{'port':'tcp://100.118.134.43:3389','security_layer':'tls'})
  self.assertEqual(out.split('[Other]')[1],s.split('[Other]')[1])
  self.assertEqual(out,m.update(out,'Globals',{'port':'tcp://100.118.134.43:3389','security_layer':'tls'}))
 def test_reject_ambiguous(self):
  with self.assertRaises(RuntimeError): m.update('[Globals]\nport=1\nport=2\n','Globals',{'port':'3'})
 def test_tailnet(self):
  s={'BackendState':'Running','Self':{'TailscaleIPs':['100.118.134.43']},'Peer':{'a':{'TailscaleIPs':['100.90.207.58']}}}
  self.assertEqual(m.select_ip(s,'auto','100.90.207.58'),'100.118.134.43')
  with self.assertRaises(RuntimeError): m.select_ip(s,'100.119.208.88','100.90.207.58')
  with self.assertRaises(RuntimeError): m.select_ip(s,'auto','100.90.207.59')
 def test_report_no_secret(self):
  report=m.asset_report('edge1','nvidia','100.118.134.43')
  self.assertEqual(report['assets'][1]['protocol'],'rdp')
  self.assertNotIn('password',str(report).lower())
 def test_backup_restore(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d); original=root/'existing'; original.write_text('original')
   new=root/'new'
   b=m.Backup.__new__(m.Backup);b.root=root/'backup';b.root.mkdir();b.items=[]
   import os
   b.write(original,'changed',0o600,os.getuid(),os.getgid())
   b.write(new,'created',0o600,os.getuid(),os.getgid())
   b.restore()
   self.assertEqual(original.read_text(),'original')
   self.assertFalse(new.exists())
if __name__=='__main__': unittest.main()
