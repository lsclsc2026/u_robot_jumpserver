import unittest
import edge_finalize as m
class Tests(unittest.TestCase):
 def test_rules(self):
  s=m.rules('100.90.207.58')
  self.assertIn('tcp dport { 22, 3389 } drop',s)
  self.assertIn('iifname "tailscale0" ip saddr 100.90.207.58 accept',s)
  self.assertNotIn('flush ruleset',s)
  self.assertNotIn('counter',s)
 def test_invalid_ip(self):
  with self.assertRaises(ValueError): m.rules('100.1.1.1; flush ruleset')
 def test_pending(self):
  m.require_pending({'phase':'pending','deadline':200},100)
  with self.assertRaises(RuntimeError): m.require_pending({'phase':'pending','deadline':105},100)
  with self.assertRaises(RuntimeError): m.require_pending({'phase':'rolled_back','deadline':200},100)
 def test_nomachine_manual_exit_one(self):
  self.assertTrue(m.manual_mode_ok(1, 'NX> 804 Startup of nxserver is: Manual.\n'))
  self.assertFalse(m.manual_mode_ok(1, 'Permission denied'))
  self.assertFalse(m.manual_mode_ok(2, 'NX> 804 Startup of nxserver is: Manual.'))
  self.assertFalse(m.manual_mode_ok(0, 'Startup of nxserver is: Automatic.'))
 def test_nomachine_stopped(self):
  stopped='NX> 111 NoMachine server has been shut down.\nNX> 162 Disabled service: nxserver.\nNX> 162 Disabled service: nxnode.\nNX> 162 Disabled service: nxd.'
  self.assertTrue(m.nx_stopped(stopped))
  self.assertFalse(m.nx_stopped(stopped.replace('Disabled service: nxd.', 'Enabled service: nxd.')))
if __name__=='__main__': unittest.main()
