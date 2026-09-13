#!/usr/bin/env python3
"""Read-only import diagnosis. Full errors stay in a root-only directory."""
import datetime,json,os,re,subprocess,sys
from pathlib import Path
NAMES=input('Usernames to inspect (space-separated): ').split()
if not NAMES: sys.exit('No usernames supplied; nothing queried.')
if os.geteuid()!=0: sys.exit('Run with sudo python3; read-only diagnosis.')
os.umask(0o077)
out=Path('/var/lib/jumpserver-laptop-setup')/('import-diagnostic-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S'))
out.mkdir(mode=0o700)
code="""
import json
from users.models import User
names = NAMES_PLACEHOLDER
rows=list(User.objects.filter(username__in=names).values('username','is_active','need_update_password','date_expired','source'))
print('SAFE_BEGIN')
print(json.dumps({'existing_users':rows,'missing_users':sorted(set(names)-{r['username'] for r in rows})},default=str,indent=2))
print('SAFE_END')
""".replace('NAMES_PLACEHOLDER',repr(NAMES))
r=subprocess.run(['docker','exec','-i','jms_core','python','/opt/jumpserver/apps/manage.py','shell','-i','python'],input='exec('+repr(code)+')\n',text=True,capture_output=True,timeout=90)
(out/'user-query.log').write_text(r.stdout+'\n'+r.stderr)
if r.returncode==0 and 'SAFE_BEGIN\n' in r.stdout:
 print(r.stdout.split('SAFE_BEGIN\n',1)[1].split('SAFE_END',1)[0])
else: print('USER_QUERY_FAILED: protected log saved')
# Capture recent stdout and recent application log tails, without printing raw lines.
r=subprocess.run(['docker','logs','--since','90m','--tail','3000','jms_core'],text=True,capture_output=True,timeout=30)
raw=r.stdout+'\n'+r.stderr
collect="""
from pathlib import Path
p=Path('/opt/jumpserver/data/logs')
files=sorted((x for x in p.rglob('*.log') if x.is_file()),key=lambda x:x.stat().st_mtime,reverse=True)[:6]
for f in files:
 print('LOG_FILE:',f.name)
 with f.open('rb') as stream:
  stream.seek(max(0,f.stat().st_size-256000))
  print(stream.read().decode('utf-8',errors='replace'))
"""
r=subprocess.run(['docker','exec','-i','jms_core','python','-'],input=collect,text=True,capture_output=True,timeout=30)
raw+='\n'+r.stdout+'\n'+r.stderr
(out/'core-errors.log').write_text(raw)
# Report source locations and exception classes. Messages/literal values stay private.
frames=[];errors=[];constraints=set()
for line in raw.splitlines():
 m=re.search(r'File "([^"\n]+\.py)", line (\d+), in ([\w<>]+)',line)
 if m: frames.append({'file':m[1],'line':int(m[2]),'function':m[3]})
 m=re.match(r'^\s*([\w.]+(?:Error|Exception)):',line)
 if m: errors.append(m[1])
 m=re.search(r'(?:unique|foreign key|check) constraint "([a-zA-Z0-9_]+)"',line)
 if m: constraints.add(m[1])
print(json.dumps({'exception_types':sorted(set(errors)),'database_constraints':sorted(constraints),'recent_traceback_frames':frames[-24:],'full_log_saved_locally':str(out)},indent=2))
print('IMPORT_DIAG_COMPLETE: read-only; no account/password changes.')
