import unittest
from unittest.mock import patch
import subprocess
import edge_wizard as m
class TailscaleTests(unittest.TestCase):
 def test_installed_skips_installer_but_starts_and_up(self):
  with patch.object(m.shutil,'which',return_value='/usr/bin/tailscale'), patch.object(m,'run') as r:
   m.ensure_tailscale()
   self.assertEqual([c.args for c in r.call_args_list],[('systemctl','enable','--now','tailscaled'),('tailscale','up')])
 def test_missing_installs_then_up(self):
  def which(name):
   if name=='tailscale':
    which.n+=1
    return None if which.n==1 else '/usr/bin/tailscale'
   return '/usr/bin/'+name
  which.n=0
  with patch.object(m.shutil,'which',side_effect=which),patch.object(m,'run') as r:
   m.ensure_tailscale()
   calls=[c.args for c in r.call_args_list]
   self.assertTrue(any(c[0]=='curl' and 'https://tailscale.com/install.sh' in c for c in calls))
   self.assertTrue(any(c[0]=='sh' for c in calls))
   self.assertEqual(calls[-1],('tailscale','up'))
 def test_download_failure_never_runs_shell(self):
  with patch.object(m.shutil,'which',side_effect=lambda n: None if n=='tailscale' else '/usr/bin/'+n),patch.object(m,'run',side_effect=subprocess.CalledProcessError(1,'curl')) as r:
   with self.assertRaises(subprocess.CalledProcessError): m.ensure_tailscale()
   self.assertFalse(any(c.args[0]=='sh' for c in r.call_args_list))
if __name__=='__main__': unittest.main()
