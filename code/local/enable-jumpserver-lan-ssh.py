#!/usr/bin/env python3
"""Expose the existing Koko SSH service on the approved LAN IP only."""
import datetime,fcntl,importlib.util,json,os,shutil,socket,threading
from pathlib import Path
spec=importlib.util.spec_from_file_location('installer',Path(__file__).resolve().with_name('install-jumpserver-laptop.py'))
b=importlib.util.module_from_spec(spec);spec.loader.exec_module(b)
LAN='172.18.14.69'

def ports(c):
 return {(s,p.get('host_ip','0.0.0.0'),str(p['published']),str(p['target']),p.get('protocol','tcp')) for s,v in c['services'].items() for p in v.get('ports',[])}

def main():
 b.require(os.geteuid()==0,'Run with sudo python3')
 b.require(socket.gethostname()=='ksxq-Jiaolong15K-Series-GM5XGEE','Unexpected host')
 os.umask(0o077)
 lock=open('/run/lock/jumpserver-laptop-install.lock','w');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 root=b.STATE/('lan-ssh-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f'));root.mkdir(mode=0o700)
 b.LOG=(root/'operation.log').open('x',buffering=1)
 print('Protected backup/log: '+str(root),flush=True)
 path=b.INSTALLER/'compose/laptop.yml';backup=root/'laptop.yml'
 changed=False;stop=threading.Event()
 def heartbeat():
  while not stop.wait(20): print('Waiting for Koko health; keep terminal open.',flush=True)
 threading.Thread(target=heartbeat,daemon=True).start()
 try:
  print('1/3 Verify address, configuration and intended port change',flush=True)
  address=json.loads(b.run(['ip','-j','-4','address','show','dev','enp2s0'],capture=True))
  b.require(any(a['local']==LAN for i in address for a in i['addr_info']),'LAN address changed')
  before=ports(json.loads(b.compose('config','--format','json',capture=True)))
  extra={('koko',LAN,'2222','2222','tcp')}
  b.require(('koko','127.0.0.1','2222','2222','tcp') in before,'Expected loopback mapping missing')
  b.require(path.is_file() and not path.is_symlink(),'Unexpected override file')
  shutil.copy2(path,backup)
  source=path.read_text()
  if not extra<=before:
   with socket.socket() as s:s.bind((LAN,2222))
   b.require(source.count('  koko:\n')==1,'Expected one Koko override')
   block='    ports:\n      - "'+LAN+':2222:2222"\n'
   source=source.replace('  koko:\n','  koko:\n'+block,1)
   changed=True;b.write_private(path,source)
  after=ports(json.loads(b.compose('config','--format','json',capture=True)))
  b.require(after==before|extra,'Unexpected port change')
  print('2/3 Apply Koko configuration and verify health (active sessions may disconnect)',flush=True)
  b.compose('up','-d','--no-deps','--pull','never','--wait','--wait-timeout','180','koko',timeout=240)
  print('3/3 Verify SSH banners on LAN and loopback',flush=True)
  for host in (LAN,'127.0.0.1'):
   with socket.create_connection((host,2222),timeout=10) as s:
    banner=s.recv(255).decode('ascii',errors='replace').strip()
   b.require(banner.startswith('SSH-2.0-'),'Missing SSH banner')
   print('SSH_PORT_OK: '+host+':2222 '+banner,flush=True)
  scan=b.run(['ssh-keyscan','-T','5','-p','2222',LAN],capture=True,timeout=25)
  b.require(bool(scan),'Could not obtain SSH host keys')
  fingerprint=b.run(['ssh-keygen','-lf','-'],capture=True,input_text=scan+'\n',timeout=10)
  print('SSH host key fingerprints:\n'+fingerprint,flush=True)
  print('LAN_SSH_ENTRY_COMPLETE: authenticated asset connection and audit verification pending.',flush=True)
 except Exception as e:
  print('LAN_SSH_STOPPED: '+str(e),flush=True)
  if changed:
   try:
    shutil.copy2(backup,path)
    b.compose('up','-d','--no-deps','--pull','never','--wait','--wait-timeout','180','koko',timeout=240)
    print('Previous Koko configuration restored.',flush=True)
   except Exception: print('ROLLBACK_NEEDS_ATTENTION: '+str(root),flush=True)
  raise SystemExit(1)
 finally:stop.set();b.LOG.close()
if __name__=='__main__':main()
