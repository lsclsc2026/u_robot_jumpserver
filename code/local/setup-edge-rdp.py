#!/usr/bin/env python3
"""Ubuntu 22.04 Jetson: isolated Xfce RDP pilot, accessible only from bastion.
Run interactively with sudo; password is entered through passwd, never logged.
"""
import re,datetime,fcntl,os,pwd,shutil,socket,subprocess,sys
from pathlib import Path

def run(*args):subprocess.run(args,check=True)
def write(p,s,mode=0o644):
 p=Path(p);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(s);p.chmod(mode)
def update_section(text,section,changes):
 pattern=r'(?ms)^\['+re.escape(section)+r'\][ \t]*\r?\n.*?(?=^\[|\Z)'
 m=re.search(pattern,text)
 if not m:raise RuntimeError('Missing section '+section)
 block=m.group()
 for key,value in changes.items():
  expr=r'(?mi)^'+re.escape(key)+r'[ \t]*=.*$'
  block,n=re.subn(expr,lambda _:key+'='+value,block)
  if n!=1:raise RuntimeError('Expected one '+section+'.'+key)
 return text[:m.start()]+block+text[m.end():]

def main():
 if os.geteuid()!=0:sys.exit('Run with sudo python3')
 if not sys.stdin.isatty():sys.exit('Run interactively to set the desktop password')
 lock=open('/run/lock/jumpserver-rdp-setup.lock','w');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 os_release=Path('/etc/os-release').read_text()
 assert 'VERSION_ID="22.04"' in os_release and 'ID=ubuntu' in os_release,'Expected Ubuntu 22.04'
 assert os.uname().machine=='aarch64','Expected Jetson ARM64'
 assert '100.119.208.88' in subprocess.check_output(['ip','-4','addr','show','tailscale0'],text=True),'Unexpected target'
 assert shutil.disk_usage('/').free>4*1024**3,'Need at least 4 GiB free'
 state=Path('/var/lib/jumpserver-rdp-pilot');state.mkdir(mode=0o700,exist_ok=True)
 try:
  pwd.getpwnam('jmsdesk')
  assert (state/'account-created').exists(),'Existing jmsdesk account needs review; no changes made'
 except KeyError:pass
 print('1/5 Restrict TCP 3389 before installing its service',flush=True)
 if not shutil.which('nft'):sys.exit('nft is required; no RDP service installed')
 rule='''table inet jumpserver_rdp_pilot {
 chain input {
  type filter hook input priority -10; policy accept;
  tcp dport 3389 iifname "tailscale0" ip saddr 100.90.207.58 accept
  tcp dport 3389 drop
 }
}
'''
 write('/etc/jumpserver-rdp-pilot.nft',rule)
 write('/usr/local/sbin/jumpserver-rdp-guard','''#!/bin/sh
set -eu
if /usr/sbin/nft list table inet jumpserver_rdp_pilot >/dev/null 2>&1; then
  exit 0
fi
exec /usr/sbin/nft -f /etc/jumpserver-rdp-pilot.nft
''',0o755)
 write('/etc/systemd/system/jumpserver-rdp-guard.service','''[Unit]
Description=Restrict RDP pilot to JumpServer over Tailscale
Before=xrdp.service
[Service]
Type=oneshot
ExecStart=/usr/local/sbin/jumpserver-rdp-guard
RemainAfterExit=yes
[Install]
WantedBy=multi-user.target
''')
 run('nft','--check','-f','/etc/jumpserver-rdp-pilot.nft')
 run('systemctl','daemon-reload');run('systemctl','enable','--now','jumpserver-rdp-guard')
 print('2/5 Install Ubuntu packages (no distribution or NVIDIA driver upgrade)',flush=True)
 run('apt-get','update')
 run('env','DEBIAN_FRONTEND=noninteractive','apt-get','install','-y','--no-install-recommends','--no-remove',
     'xrdp','xorgxrdp','xfce4-session','xfce4-panel','xfce4-settings','xfwm4','xfdesktop4','xfce4-terminal','thunar','dbus-x11','ssl-cert')
 run('systemctl','stop','xrdp')
 print('3/5 Create restricted desktop account and configure independent Xfce session',flush=True)
 try:pwd.getpwnam('jmsdesk')
 except KeyError:
  run('useradd','--create-home','--shell','/bin/bash','jmsdesk');(state/'account-created').touch(mode=0o600)
 run('groupadd','-f','jms-rdp-users');run('usermod','-aG','jms-rdp-users','jmsdesk');run('usermod','-aG','ssl-cert','xrdp')
 u=pwd.getpwnam('jmsdesk')
 session=Path(u.pw_dir)/'.xsession'
 write(session,'#!/bin/sh\nunset DBUS_SESSION_BUS_ADDRESS\nunset SESSION_MANAGER\nexec dbus-run-session -- startxfce4\n',0o700)
 os.chown(session,u.pw_uid,u.pw_gid)
 groups=subprocess.check_output(['id','-nG','jmsdesk'],text=True).split()
 assert not set(groups)&{'sudo','docker','lxd','root'},'Desktop account has privileged groups'
 stamp=datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
 for filename,section,changes in [
  ('xrdp.ini','Globals',{'port':'tcp://100.119.208.88:3389','security_layer':'tls','crypt_level':'high'}),
  ('sesman.ini','Security',{'AllowRootLogin':'false','TerminalServerUsers':'jms-rdp-users','AlwaysGroupCheck':'true'})]:
  p=Path('/etc/xrdp')/filename;shutil.copy2(p,state/(filename+'.'+stamp))
  p.write_text(update_section(p.read_text(),section,changes))
 write('/usr/local/sbin/jumpserver-rdp-wait-address','''#!/bin/sh
set -eu
i=0
while [ "$i" -lt 45 ]; do
 if /usr/sbin/ip -4 address show dev tailscale0 2>/dev/null | /usr/bin/grep -q 'inet 100.119.208.88/'; then exit 0; fi
 i=$((i + 1))
 sleep 1
done
exit 1
''',0o755)
 write('/etc/systemd/system/xrdp.service.d/jumpserver.conf','''[Unit]
Requires=jumpserver-rdp-guard.service
After=jumpserver-rdp-guard.service tailscaled.service network-online.target
Wants=tailscaled.service network-online.target
[Service]
ExecStartPre=/usr/local/sbin/jumpserver-rdp-wait-address
Restart=on-failure
RestartSec=10
''')
 print('4/5 Set jmsdesk password locally; later enter it into the JumpServer asset account',flush=True)
 run('passwd','jmsdesk')
 print('5/5 Start services and verify the restricted listener',flush=True)
 run('systemctl','daemon-reload');run('systemctl','restart','xrdp-sesman');run('systemctl','enable','--now','xrdp')
 run('systemctl','is-active','xrdp','xrdp-sesman','jumpserver-rdp-guard')
 listeners=subprocess.check_output(['ss','-lnt'],text=True)
 selected=[line for line in listeners.splitlines() if ':3389' in line]
 assert len(selected)==1 and '100.119.208.88:3389' in selected[0], 'Unexpected RDP listener'
 print(selected[0]);run('nft','list','table','inet','jumpserver_rdp_pilot')
 print('EDGE_RDP_PREPARED: independent jmsdesk desktop; GUI login and recording not yet verified.')
 print('Rollback service exposure: sudo systemctl disable --now xrdp')
if __name__=='__main__':
 try:main()
 except Exception as e:
  subprocess.run(['systemctl','stop','xrdp'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
  print('EDGE_RDP_STOPPED:',type(e).__name__,str(e));sys.exit(1)
