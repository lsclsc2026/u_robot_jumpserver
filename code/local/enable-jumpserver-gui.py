#!/usr/bin/env python3
"""Enable pinned official Lion, preserve LAN/Tailscale/SSH port mappings."""
import datetime,fcntl,importlib.util,json,os,re,shutil,socket,threading,subprocess
from pathlib import Path
spec=importlib.util.spec_from_file_location('installer',Path(__file__).resolve().with_name('install-jumpserver-laptop.py'))
b=importlib.util.module_from_spec(spec);spec.loader.exec_module(b)
MANIFEST='sha256:2836cf9a4720213f7b3a81cdd170658fe5ad2ec2a87aafb122fb444908fb8bda'
ARCHIVE_SHA='0cef28d45727e3cb9aaff2548c1bdc1de123465ebc2c930f4b74c381ddff2a0b'
def ports(c):
 return {(s,p.get('host_ip','0.0.0.0'),str(p['published']),str(p['target'])) for s,v in c['services'].items() for p in v.get('ports',[])}
def flag(text):
 b.require(len(re.findall(r'^LION_ENABLED=.*$',text,re.M))==1,'Expected exactly one LION_ENABLED')
 return re.sub(r'^LION_ENABLED=.*$','LION_ENABLED=1',text,flags=re.M)
def main():
 b.require(os.geteuid()==0,'Run with sudo python3')
 b.require(socket.gethostname()=='ksxq-Jiaolong15K-Series-GM5XGEE','Unexpected host')
 os.umask(0o077)
 lock=open('/run/lock/jumpserver-laptop-install.lock','w');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 state=b.STATE/('gui-enable-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S'));state.mkdir(mode=0o700)
 b.LOG=(state/'operation.log').open('x',buffering=1)
 print('Protected backup/log: '+str(state),flush=True)
 stop=threading.Event()
 def heartbeat():
  while not stop.wait(20):print('GUI preparation running; keep terminal open.',flush=True)
 threading.Thread(target=heartbeat,daemon=True).start()
 saved=[];changed=False
 try:
  print('1/4 Verify offline Lion image and existing configuration',flush=True)
  item=json.loads((b.STAGING/'lion-image.json').read_text())
  b.require(item['ref']=='jumpserver/lion:v4.10.19-ce' and item['manifest_digest']==MANIFEST and item['file']=='jumpserver_lion_v4.10.19-ce.tar','Unexpected image metadata')
  b.require(b.digest(b.STAGING/'images'/item['file'])==ARCHIVE_SHA==item['sha256'],'Offline archive checksum mismatch')
  b.EXPECTED_IMAGES[item['ref']]=item['image_id'];b.load_offline_image(item)
  old_files=b.COMPOSE_FILES[:]
  old=ports(json.loads(b.compose('config','--format','json',capture=True)))
  for p in (b.CONFIG/'config.txt',b.CONFIG/'config_safe.txt',b.INSTALLER/'compose/laptop.yml'):
   b.require(p.is_file() and not p.is_symlink(),'Unexpected config path')
   dest=state/p.name;shutil.copy2(p,dest);saved.append((p,dest))
  changed=True
  for p in (b.CONFIG/'config.txt',b.CONFIG/'config_safe.txt'):b.write_private(p,flag(p.read_text()))
  p=b.INSTALLER/'compose/laptop.yml';text=p.read_text()
  if '  lion:\n' not in text:
   text+='  lion:\n    pull_policy: never\n    logging:\n      driver: json-file\n      options:\n        max-size: "10m"\n        max-file: "3"\n'
   b.write_private(p,text)
  b.COMPOSE_FILES.insert(4,'lion')
  after=json.loads(b.compose('config','--format','json',capture=True))
  b.require(ports(after)==old,'Unexpected exposed port change')
  b.require(after['services']['lion']['image']==item['ref'],'Unexpected Lion image')
  b.require(str(after['services']['web']['environment']['LION_ENABLED'])=='1','Web Lion routing not enabled')
  print('2/4 Start Lion and refresh web/application configuration (brief service interruption)',flush=True)
  b.compose('up','-d','--no-deps','--pull','never','--wait','--wait-timeout','600','lion','core','celery','web',timeout=720)
  print('3/4 Check Lion runtime namespace can reach the restricted RDP endpoint',flush=True)
  b.run(['docker','run','--rm','--pull','never','--network','container:jms_lion',
         '--entrypoint','/usr/bin/python3.14','jumpserver/ansible-executor:latest','-c',
         "import socket; s=socket.create_connection(('100.119.208.88',3389),10); s.close(); print('RDP_PORT_OK')"],timeout=30)
  print('4/4 Verify web health and publish safe report',flush=True)
  for ip in ('172.18.14.69','100.90.207.58'):
   code=b.run(['curl','--noproxy','*','--cacert',b.CONFIG/'nginx/cert/server.crt','--max-time','15','-sS','-o','/dev/null','-w','%{http_code}','https://'+ip+'/api/health/'],capture=True)
   b.require(code=='200','HTTPS health check failed')
  print('GUI_COMPONENTS_READY: Lion healthy; RDP network reachable; configure asset/account and validate desktop/replay next.',flush=True)
 except Exception as e:
  print('GUI_SETUP_STOPPED: '+str(e),flush=True)
  if changed:
   try:
    subprocess.run(['docker','stop','jms_lion'],stdout=b.LOG,stderr=b.LOG,timeout=60)
    for p,d in saved:shutil.copy2(d,p)
    b.COMPOSE_FILES=old_files
    b.compose('up','-d','--no-deps','--pull','never','--wait','--wait-timeout','600','core','celery','web',timeout=720)
    print('Previous configuration restored; image/data retained.',flush=True)
   except Exception:print('ROLLBACK_NEEDS_ATTENTION: '+str(state),flush=True)
  raise SystemExit(1)
 finally:stop.set();b.LOG.close()
if __name__=='__main__':main()
