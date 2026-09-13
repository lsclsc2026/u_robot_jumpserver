#!/usr/bin/env python3
"""Collect allowlisted deployment code/config; never databases, private keys or login credentials."""
import argparse, glob, hashlib, json, os, re, socket, subprocess, zipfile
from pathlib import Path
p=argparse.ArgumentParser()
p.add_argument('--role',choices=['server','edge'],required=True)
p.add_argument('--out',required=True)
a=p.parse_args()
files=[]
if a.role=='server':
    names=['install-jumpserver-laptop.py','install-jumpserver-laptop.before-image-id-fix.py','add-jumpserver-executor.py','enable-jumpserver-gui.py','enable-jumpserver-lan.py','enable-jumpserver-lan-ssh.py','diagnose-jumpserver-import.py','diagnose-jumpserver-login.py','diagnose-jumpserver-operator-login.py','recover-jumpserver-admin.sh','prepare-jumpserver-runtime.sh','setup-jumpserver-usb.py','ntfs-readonly-check.sh']
    files += ['/home/ksxq/'+n for n in names]
    files += ['/usr/local/sbin/jumpserver-laptop-compose']
    files += glob.glob('/opt/jumpserver-installer-v4.10.19/compose/*.yml')
    for n in ['DEPLOYMENT-NOTES.md','PROGRESS.md','jumpserver-server.crt']:
        files.append('/home/ksxq/jumpserver-deployment/'+n)
else:
    for h in ['/home/lumos','/home/nvidia']:
        for n in ['setup-edge-rdp.py','fix-edge-rdp-start.py','fix-edge-rdp-parameters.py','fix-edge-xsessionrc.py','switch-jmsdesk-ubuntu.py','configure-nvidia-xrdp.py','restrict-edge-ssh.py','startwm.sh','edge_wizard.py','setup_edge_desktop.py','edge_finalize.py','diagnose-edge-access.py']:
            files.append(h+'/'+n)
        for pattern in ['jumpserver-edge-tools/*.py','jumpserver-edge-tools-v2/*.py']:
            files += glob.glob(h+'/'+pattern)
    for pattern in ['/usr/local/sbin/jms-*','/usr/local/sbin/jumpserver-rdp-*','/etc/jms-*.nft','/etc/jumpserver-rdp*.nft','/etc/systemd/system/jms-*.service','/etc/systemd/system/jumpserver-*.service','/etc/systemd/system/xrdp*.service.d/*.conf','/var/lib/jms-edge-finalize/manage.py','/var/lib/jms-edge-finalize/rules.nft','/var/lib/jumpserver-ssh-gate/manage.py','/var/lib/jumpserver-ssh-gate/rules.nft']:
        files += glob.glob(pattern)
    files += ['/etc/xrdp/xrdp.ini','/etc/xrdp/sesman.ini']

def redact(text):
    # Config fields only: source-code lines are not rewritten.
    lines=[]
    for line in text.splitlines(keepends=True):
        if re.match(r'^\s*(?:[-]\s*)?[\w.-]*(?:password|passwd|secret|token|private_key|bootstrap_token)[\w.-]*\s*[:=]',line,re.I):
            prefix=re.split(r'[:=]',line,maxsplit=1)[0]
            line=prefix+'= <REDACTED>\n'
        lines.append(line)
    return ''.join(lines)

out=Path(a.out).resolve()
out.parent.mkdir(parents=True,exist_ok=True)
report={'hostname':socket.gethostname(),'role':a.role,'uid':os.geteuid(),'files':[],'commands':[],'excluded':['private configuration config.txt/config_safe.txt','SSH/TLS private keys','password files','Tailscale state','database and session recordings','general system and application logs']}
with zipfile.ZipFile(out,'x',compression=zipfile.ZIP_DEFLATED) as z:
    os.chmod(out,0o600)
    for name in sorted(set(files)):
        f=Path(name)
        row={'path':name}
        try:
            if f.is_symlink(): raise ValueError('symlink skipped')
            if not f.is_file(): raise FileNotFoundError('not present')
            if f.stat().st_size>2_000_000: raise ValueError('too large for configuration collection')
            data=f.read_bytes()
            row['source_sha256']=hashlib.sha256(data).hexdigest()
            if f.suffix in ['.ini','.yml','.conf']:
                data=redact(data.decode()).encode()
            z.writestr('files/'+name.lstrip('/'),data)
            row['status']='collected'
            row['archive_sha256']=hashlib.sha256(data).hexdigest()
        except (OSError,ValueError,UnicodeError) as e:
            row['status']='unavailable';row['reason']=str(e)
        report['files'].append(row)
    commands=[['uname','-a'],['ip','-br','address'],['systemctl','is-active','ssh','tailscaled','xrdp','xrdp-sesman'],['systemctl','is-enabled','ssh','tailscaled','xrdp']]
    if a.role=='edge':
        commands += [['/usr/sbin/nft','list','table','inet',t] for t in ['jms_access_guard','jms_ssh_gate','jumpserver_rdp_pilot','jms_edge_final']]
    for cmd in commands:
        try:
            r=subprocess.run(cmd,capture_output=True,text=True,timeout=10)
            report['commands'].append({'argv':cmd,'returncode':r.returncode,'stdout':r.stdout,'stderr':r.stderr})
        except Exception as e: report['commands'].append({'argv':cmd,'error':str(e)})
    z.writestr('collection-report.json',json.dumps(report,ensure_ascii=False,indent=2))
print(str(out))
print('Collected:',sum(r['status']=='collected' for r in report['files']))
print('Unavailable:',sum(r['status']=='unavailable' for r in report['files']))
