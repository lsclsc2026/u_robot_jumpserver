#!/usr/bin/env python3
"""Read-only incident collection; writes protected evidence files only."""
import datetime, hashlib, json, os, re, subprocess
from pathlib import Path
if os.geteuid()!=0: raise SystemExit('Run with sudo python3.')
root=Path('/var/lib/jms-access-investigation')/datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f')
root.mkdir(parents=True,mode=0o700);root.parent.chmod(0o700)
def get(name,cmd):
 try:
  r=subprocess.run(cmd,text=True,capture_output=True,timeout=35)
  data=r.stdout+r.stderr;code=r.returncode
 except Exception as e: data=str(e);code=-1
 p=root/name;p.write_text(data);p.chmod(0o600)
 return code,data
report={'checked_at':datetime.datetime.now().astimezone().isoformat(),'evidence_directory':str(root)}
for key,cmd in [('hostname',['hostname']),('boot_time',['uptime','-s']),('tailscale_ip',['tailscale','ip','-4']),('addresses',['ip','-br','address'])]:
 _,s=get(key+'.txt',cmd);report[key]=s.strip()
p=Path('/var/lib/jms-edge-finalize/state.json')
if p.exists():
 raw=p.read_text();(root/'state.json').write_text(raw)
 try:
  obj=json.loads(raw);report['finalize_state']={k:obj.get(k) for k in ['phase','bastion_ip','deadline','timer','nomachine']}
 except Exception: report['finalize_state']='invalid JSON'
else:report['finalize_state']='missing'
_,tables=get('nft-tables.txt',['nft','list','tables']);report['nft_tables']=tables.strip()
get('nft-full.txt',['nft','-a','list','ruleset'])
code,rules=get('nft-own.txt',['nft','-a','list','table','inet','jms_edge_final'])
report['own_table_present']=code==0
report['own_rules']=rules.strip()
get('iptables-v4.txt',['iptables-save'])
get('iptables-v6.txt',['ip6tables-save'])
_,s=get('listeners.txt',['ss','-lntup'])
report['remote_listeners']=[line for line in s.splitlines() if re.search(r':(?:22|3389|4000)\b',line)]
report['services']={}
for unit in ['jms-edge-final.service','nxserver.service','nftables.service','ufw.service','firewalld.service','tailscaled.service','docker.service']:
 _,s=get(unit+'.show.txt',['systemctl','show',unit,'--property=LoadState,ActiveState,SubState,UnitFileState,ActiveEnterTimestamp,InactiveEnterTimestamp,ExecMainStartTimestamp,ExecMainExitTimestamp,NRestarts,Result,FragmentPath,DropInPaths'])
 report['services'][unit]=dict(l.split('=',1) for l in s.splitlines() if '=' in l)
 get(unit+'.unit.txt',['systemctl','cat',unit])
get('timers.txt',['systemctl','list-timers','--all','--no-pager'])
get('boots.txt',['journalctl','--list-boots','--no-pager'])
report['relevant_unit_events']=[]
for unit in ['jms-edge-final.service','nxserver.service','jms-edge-rollback-*.service','jms-edge-rollback-*.timer','nftables.service','ufw.service','firewalld.service']:
 _,s=get(unit.replace('*','ALL')+'.journal.jsonl',['journalctl','--since','7 days ago','-u',unit,'--no-pager','-o','json','-n','150'])
 for line in s.splitlines():
  try: row=json.loads(line)
  except Exception: continue
  msg=str(row.get('MESSAGE',''))
  if any(x in msg for x in ['FIREWALL_ROLLED_BACK','Not committed','Traceback','Error','Failed','Started','Stopped','Starting','Stopping','timed out']):
   # Lifecycle labels only; full original messages retained privately.
   labels=[x for x in ['FIREWALL_ROLLED_BACK','Not committed','Traceback','Error','Failed','Started','Stopped','Starting','Stopping','timed out'] if x in msg]
   report['relevant_unit_events'].append({'timestamp_us':row.get('__REALTIME_TIMESTAMP'),'unit':row.get('_SYSTEMD_UNIT') or row.get('UNIT') or unit,'labels':labels})
_,s=get('sudo-events.jsonl',['journalctl','--since','7 days ago','_COMM=sudo','--no-pager','-o','json','-n','1500'])
report['sudo_related_commands']=[]
for line in s.splitlines():
 try:r=json.loads(line)
 except Exception:continue
 msg=str(r.get('MESSAGE',''))
 if 'COMMAND=' not in msg:continue
 keywords=[k for k in ['edge_finalize','manage.py','nft','iptables','nxserver','firewalld','ufw','systemctl'] if k in msg]
 if keywords:
  report['sudo_related_commands'].append({'timestamp_us':r.get('__REALTIME_TIMESTAMP'),'uid':r.get('_UID'),'keywords':keywords})
report['files']={}
for name in ['/var/lib/jms-edge-finalize/manage.py','/var/lib/jms-edge-finalize/rules.nft','/etc/systemd/system/jms-edge-final.service','/home/nvidia/jumpserver-edge-tools/edge_finalize.py','/usr/NX/etc/server.cfg']:
 p=Path(name)
 if p.is_file():
  st=p.stat();report['files'][name]={'mtime':datetime.datetime.fromtimestamp(st.st_mtime).astimezone().isoformat(),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
  if name.endswith('server.cfg'):
   text=p.read_text(errors='replace');report['nx_startup_settings']=[l for l in text.splitlines() if re.match(r'^\s*(Start\w*|EnableNetworkBroadcast)\s+',l) and not l.lstrip().startswith('#')]
for nx in ['/usr/NX/bin/nxserver','/etc/NX/nxserver']:
 if Path(nx).is_file():
  _,s=get('nx-status.txt',[nx,'--status']);report['nx_status']=s.strip();break
for p in root.iterdir():p.chmod(0o600)
(root/'summary.json').write_text(json.dumps(report,ensure_ascii=False,indent=2));(root/'summary.json').chmod(0o600)
print(json.dumps(report,ensure_ascii=False,indent=2))
print('READ_ONLY_COMPLETE: firewall/services/accounts unchanged; raw logs retained privately. Do not publish raw logs without review.')
