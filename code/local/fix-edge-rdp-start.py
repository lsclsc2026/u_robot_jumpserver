#!/usr/bin/env python3
"""Use supported foreground mode to avoid xrdp forking/PID tracking failure."""
import datetime,os,shutil,subprocess,sys,time
from pathlib import Path

def run(*args):return subprocess.run(args,check=True)
def main():
 if os.geteuid()!=0:sys.exit('Run with sudo python3')
 assert Path('/var/lib/jumpserver-rdp-pilot/account-created').exists(),'Expected RDP pilot not found'
 assert '100.119.208.88' in subprocess.check_output(['ip','-4','addr','show','tailscale0'],text=True),'Unexpected target'
 for binary in ('xrdp','xrdp-sesman'):
  p=subprocess.run(['/usr/sbin/'+binary,'--help'],capture_output=True,text=True)
  assert '--nodaemon' in p.stdout+p.stderr,'Foreground mode unsupported'
 run('nft','list','table','inet','jumpserver_rdp_pilot')
 root=Path('/var/lib/jumpserver-rdp-pilot')/('startup-fix-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S'));root.mkdir(mode=0o700)
 paths=[]
 print('1/3 Stop the failed RDP pair; preserve existing configuration',flush=True)
 run('systemctl','stop','xrdp.service','xrdp-sesman.service')
 try:
  for service,options in [('xrdp','XRDP_OPTIONS'),('xrdp-sesman','SESMAN_OPTIONS')]:
   p=Path('/etc/systemd/system')/(service+'.service.d/foreground.conf')
   assert not p.is_symlink(),'Unexpected symlink'
   backup=root/(service+'.conf')
   existed=p.exists()
   if existed:shutil.copy2(p,backup)
   paths.append((p,backup,existed));p.parent.mkdir(parents=True,exist_ok=True)
   p.write_text('[Service]\nType=simple\nPIDFile=\nExecStart=\nExecStart=/usr/sbin/'+service+' --nodaemon $'+options+'\nExecStop=\n')
   p.chmod(0o644)
  print('2/3 Start xrdp and let systemd start its session-manager dependency',flush=True)
  run('systemctl','daemon-reload')
  run('systemctl','reset-failed','xrdp','xrdp-sesman')
  run('systemctl','enable','xrdp','xrdp-sesman')
  run('systemctl','start','xrdp')
  time.sleep(5)
  print('3/3 Verify running processes and restricted listener',flush=True)
  run('systemctl','is-active','xrdp','xrdp-sesman','jumpserver-rdp-guard')
  listeners=subprocess.check_output(['ss','-lnt'],text=True)
  selected=[s for s in listeners.splitlines() if ':3389' in s]
  assert len(selected)==1 and '100.119.208.88:3389' in selected[0],'Unexpected RDP listener'
  print(selected[0])
  run('systemctl','show','xrdp','xrdp-sesman','--property=Type,MainPID,SubState')
  print('EDGE_RDP_START_FIXED: service and listener verified; desktop authentication/replay pending.')
 except Exception:
  subprocess.run(['systemctl','stop','xrdp','xrdp-sesman'])
  for p,backup,existed in paths:
   if existed:shutil.copy2(backup,p)
   else:p.unlink(missing_ok=True)
  subprocess.run(['systemctl','daemon-reload'])
  print('Previous unit settings restored; RDP left stopped. Backup:',root)
  raise
if __name__=='__main__':main()
