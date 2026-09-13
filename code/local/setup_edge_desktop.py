#!/usr/bin/env python3
"""Ubuntu 22.04 edge SSH + Ubuntu/GNOME RDP provisioning. Default: read-only check."""
import argparse, datetime, ipaddress, json, os, pwd, re, shutil, subprocess, sys, tempfile, time
from pathlib import Path

def update(text, section, changes):
    lines = text.splitlines(keepends=True)
    headers = [(i, m.group(1).strip().lower()) for i, line in enumerate(lines)
               if (m := re.match(r'^\s*\[([^]]+)\]\s*(?:[;#].*)?$', line.strip()))]
    matches = [(i, next((j for j, _ in headers if j > i), len(lines)))
               for i, name in headers if name == section.lower()]
    if len(matches) != 1:
        raise RuntimeError('Expected exactly one section: ' + section)
    start, end = matches[0]
    for key, value in changes.items():
        pattern = re.compile(r'^\s*' + re.escape(key) + r'\s*=', re.I)
        hits = [i for i in range(start+1, end) if pattern.match(lines[i])]
        if len(hits) > 1:
            raise RuntimeError('Duplicate setting: ' + key)
        if hits:
            lines[hits[0]] = f'{key}={value}\n'
        else:
            if end and not lines[end-1].endswith('\n'):
                lines[end-1] += '\n'
            lines.insert(end, f'{key}={value}\n')
            end += 1
    return ''.join(lines)

SESSION = '''#!/bin/sh
unset DBUS_SESSION_BUS_ADDRESS
unset SESSION_MANAGER
export GNOME_SHELL_SESSION_MODE=ubuntu
export XDG_CURRENT_DESKTOP=ubuntu:GNOME
export XDG_SESSION_DESKTOP=ubuntu
export DESKTOP_SESSION=ubuntu
export GDMSESSION=ubuntu
export XDG_SESSION_TYPE=x11
export XDG_CONFIG_DIRS=/etc/xdg/xdg-ubuntu:/etc/xdg
export XDG_DATA_DIRS=/usr/share/ubuntu:/usr/local/share:/usr/share:/var/lib/snapd/desktop
exec /usr/bin/dbus-run-session -- /usr/bin/gnome-session --session=ubuntu --builtin
'''

def run(*args, capture=False, check=True):
    return subprocess.run(args, text=True, capture_output=capture, check=check)

def select_ip(status, requested, bastion):
    ipaddress.IPv4Address(bastion)
    if status.get('BackendState') != 'Running':
        raise RuntimeError('Tailscale must already be authenticated and Running.')
    ips=[v for v in status.get('Self',{}).get('TailscaleIPs',[]) if ':' not in v]
    if not ips: raise RuntimeError('No Tailscale IPv4 address.')
    chosen=ips[0] if requested=='auto' else str(ipaddress.IPv4Address(requested))
    if chosen not in ips: raise RuntimeError('Requested IP does not belong to this device.')
    if not any(bastion in p.get('TailscaleIPs',[]) for p in (status.get('Peer') or {}).values()):
        raise RuntimeError('Bastion is not visible in this tailnet; fix membership/policy first.')
    return chosen

def asset_report(name,user,ip):
    return {'device':name,'address':ip,'linux_user':user,'assets':[
        {'name':name,'platform':'Linux','protocol':'ssh','port':22,'account':user},
        {'name':name+'-desktop','platform':'Ubuntu-RDP','protocol':'rdp','port':3389,'account':user}],
        'pending':['JumpServer credentials and authorization','Real SSH and desktop login',
                   'SSH audit and desktop replay','NoMachine shutdown and ingress firewall','Reboot recovery']}

class Backup:
    def __init__(self):
        self.root=Path('/var/backups')/('jms-edge-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
        self.root.mkdir(mode=0o700)
        self.items=[]
    def save(self,p):
        p=Path(p)
        if any(x['path']==str(p) for x in self.items): return
        if p.is_symlink(): raise RuntimeError('Refusing symlink: '+str(p))
        item={'path':str(p),'backup':None}
        if p.exists():
            if not p.is_file(): raise RuntimeError('Not a regular file: '+str(p))
            item['backup']=str(len(self.items))+'.bak'
            shutil.copy2(p,self.root/item['backup'])
            st=p.stat();item.update(uid=st.st_uid,gid=st.st_gid,mode=st.st_mode & 0o777)
        self.items.append(item)
        (self.root/'manifest.json').write_text(json.dumps(self.items,indent=2))
    def write(self,p,data,mode=0o644,uid=0,gid=0):
        p=Path(p);self.save(p);p.parent.mkdir(parents=True,exist_ok=True)
        fd,tmp=tempfile.mkstemp(prefix='.jms-',dir=p.parent)
        try:
            with os.fdopen(fd,'w') as f:
                os.fchmod(f.fileno(),mode);os.fchown(f.fileno(),uid,gid)
                f.write(data);f.flush();os.fsync(f.fileno())
            os.replace(tmp,p)
        finally:
            if os.path.exists(tmp): os.unlink(tmp)
    def restore(self):
        for item in reversed(self.items):
            p=Path(item['path'])
            if item['backup']:
                if p.is_symlink(): p.unlink()
                shutil.copy2(self.root/item['backup'],p)
                os.chown(p,item['uid'],item['gid']);os.chmod(p,item['mode'])
            else: p.unlink(missing_ok=True)

def main():
    a=argparse.ArgumentParser(description=__doc__)
    a.add_argument('--user',required=True,help='Existing Linux developer account, e.g. nvidia or lumos')
    a.add_argument('--bastion-ip',required=True,help='Bastion Tailscale IPv4')
    a.add_argument('--edge-ip',default='auto',help='Auto-detect by default; explicit value checks target')
    a.add_argument('--name',help='Asset name prefix, default hostname')
    a.add_argument('--apply',action='store_true',help='Install/configure; without this flag only check')
    a.add_argument('--install-gnome',action='store_true',help='Explicitly allow installing ubuntu-desktop-minimal if absent')
    a.add_argument('--replace-startwm',action='store_true',help='Back up and replace a different existing user startwm.sh')
    args=a.parse_args()
    if os.geteuid()!=0: raise RuntimeError('Run with sudo python3 (also for preflight).')
    if not re.fullmatch(r'[a-z_][a-z0-9_-]*\$?',args.user): raise RuntimeError('Invalid username.')
    u=pwd.getpwnam(args.user)
    if u.pw_uid==0: raise RuntimeError('Root desktop is not supported.')
    release=dict(line.split('=',1) for line in Path('/etc/os-release').read_text().splitlines() if '=' in line)
    if release.get('ID','').strip('"')!='ubuntu' or release.get('VERSION_ID','').strip('"')!='22.04':
        raise RuntimeError('This version supports Ubuntu 22.04 only; validate other OS versions separately.')
    if not shutil.which('tailscale'): raise RuntimeError('Install and authorize Tailscale first; script never migrates tailnets.')
    status=json.loads(run('tailscale','status','--json',capture=True).stdout)
    edge=select_ip(status,args.edge_ip,args.bastion_ip)
    for warning in status.get('Health') or []: print('TAILSCALE_WARNING:',warning,flush=True)
    name=args.name or os.uname().nodename
    session=Path(u.pw_dir)/'startwm.sh'
    if session.is_symlink(): raise RuntimeError('User startwm.sh is a symlink; inspect manually.')
    if session.exists() and session.read_text()!=SESSION and not args.replace_startwm:
        raise RuntimeError('Existing startwm.sh differs; inspect it, then use --replace-startwm if intended.')
    has_gnome=Path('/usr/share/gnome-session/sessions/ubuntu.session').exists() and shutil.which('gnome-shell')
    if not has_gnome and not args.install_gnome:
        raise RuntimeError('Ubuntu GNOME absent. Review package plan, then explicitly use --install-gnome.')
    if has_gnome:
        if '--builtin' not in run('gnome-session','--help',capture=True).stdout:
            raise RuntimeError('gnome-session lacks --builtin.')
    report=asset_report(name,args.user,edge)
    print(json.dumps(report,ensure_ascii=False,indent=2),flush=True)
    for f in ['/etc/systemd/system/xrdp.service.d/jumpserver-network.conf','/etc/systemd/system/xrdp.service.d/jumpserver.conf']:
        if Path(f).exists(): print('EXISTING_OVERRIDE: '+f+' (retained; inspect any old IP/wait script).',flush=True)
    if not args.apply:
        print('PREFLIGHT_COMPLETE: no installation/configuration changes; connectivity and desktop still need testing.')
        return
    for unit in ['xrdp','xrdp-sesman']:
        if run('systemctl','is-enabled',unit,capture=True,check=False).stdout.strip().startswith('masked'):
            raise RuntimeError(unit+' is already masked; inspect before applying.')
    if run('pgrep','-x','Xorg',capture=True,check=False).returncode==0:
        print('NOTICE: Xorg is running. Applying restarts xrdp; existing RDP sessions may disconnect.',flush=True)
    backup=Backup(); print('Protected configuration backup: '+str(backup.root),flush=True)
    masked=[]
    try:
        print('1/5 Prevent RDP package auto-start; install required packages',flush=True)
        for unit in ['xrdp','xrdp-sesman']:
            run('systemctl','mask','--runtime','--now',unit);masked.append(unit)
        run('apt-get','update')
        packages=['openssh-server','curl','ca-certificates','python3','nftables','xrdp','xorgxrdp','dbus-x11']
        if not has_gnome: packages.append('ubuntu-desktop-minimal')
        run('apt-get','--no-remove','install','-y',*packages)
        if '--builtin' not in run('gnome-session','--help',capture=True).stdout: raise RuntimeError('GNOME --builtin unavailable.')
        print('2/5 Configure account groups and preserve xrdp duplicate parameters',flush=True)
        run('groupadd','-f','jms-rdp-users');run('usermod','-aG','jms-rdp-users',args.user)
        run('usermod','-aG','ssl-cert','xrdp')
        specs={Path('/etc/xrdp/xrdp.ini'):[('Globals',dict(port=f'tcp://{edge}:3389',security_layer='tls',crypt_level='high'))],
               Path('/etc/xrdp/sesman.ini'):[('Globals',dict(EnableUserWindowManager='true',UserWindowManager='startwm.sh',DefaultWindowManager='startwm.sh')),
                                           ('Security',dict(AllowRootLogin='false',TerminalServerUsers='jms-rdp-users',AlwaysGroupCheck='true'))]}
        for p,edits in specs.items():
            old=p.read_text();new=old
            for section,changes in edits: new=update(new,section,changes)
            params=lambda s:[line for line in s.splitlines() if re.match(r'^\s*param\s*=',line,re.I)]
            if params(old)!=params(new): raise RuntimeError('Xorg parameters changed unexpectedly.')
            st=p.stat();backup.write(p,new,st.st_mode & 0o777,st.st_uid,st.st_gid)
        print('3/5 Configure user Ubuntu desktop and Tailscale startup wait',flush=True)
        backup.write(session,SESSION,0o700,u.pw_uid,u.pw_gid)
        run('sh','-n',str(session))
        waiter=f'''#!/bin/sh
set -eu
n=0
while [ "$n" -lt 60 ]; do
  if ip -4 -o addr show dev tailscale0 2>/dev/null | grep -Fq ' {edge}/32 '; then
    exit 0
  fi
  n=$((n + 1))
  sleep 1
done
exit 1
'''
        backup.write('/usr/local/sbin/jms-edge-wait-tailscale',waiter,0o755)
        backup.write('/etc/systemd/system/xrdp.service.d/jms-edge-network.conf','''[Unit]
Wants=network-online.target tailscaled.service
After=network-online.target tailscaled.service
[Service]
ExecStartPre=/usr/local/sbin/jms-edge-wait-tailscale
Restart=on-failure
RestartSec=10
''')
        print('4/5 Enable services and wait for RDP listener',flush=True)
        for unit in list(masked): run('systemctl','unmask','--runtime',unit);masked.remove(unit)
        run('systemctl','daemon-reload')
        run('systemctl','enable','--now','ssh','tailscaled')
        run('systemctl','enable','xrdp','xrdp-sesman')
        run('systemctl','start','xrdp')
        ok=False
        for _ in range(30):
            listeners=run('ss','-H','-lnt','sport = :3389',capture=True).stdout.splitlines()
            endpoints=[line.split()[3] for line in listeners]
            services=all(run('systemctl','is-active','--quiet',x,check=False).returncode==0 for x in ['xrdp','xrdp-sesman'])
            if services and endpoints==[edge+':3389']: ok=True;break
            time.sleep(1)
        if not ok: raise RuntimeError('RDP did not reach the expected listener; inspect xrdp journal.')
        print('5/5 Publish connection information (no secrets)',flush=True)
        (backup.root/'asset-info.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
        print(json.dumps(report,ensure_ascii=False,indent=2))
        print('EDGE_DESKTOP_READY: services/listener verified; desktop authentication, replay, firewall and reboot tests pending.')
    except BaseException:
        run('systemctl','stop','xrdp','xrdp-sesman',check=False)
        backup.restore()
        print('FAILED: backed-up configuration restored; RDP left stopped. Installed packages/group additions are retained.',flush=True)
        raise
    finally:
        for unit in masked: run('systemctl','unmask','--runtime',unit,check=False)
        run('systemctl','daemon-reload',check=False)

if __name__=='__main__':
    try: main()
    except Exception as exc:
        print('EDGE_DESKTOP_STOPPED: '+str(exc),file=sys.stderr)
        sys.exit(1)
