#!/usr/bin/env python3
"""Own-table SSH ingress restriction, timed rollback, explicit persistence."""
import os, sys, subprocess, pathlib, shutil
TABLE = 'jms_ssh_gate'
ROOT = pathlib.Path('/var/lib/jumpserver-ssh-gate')
SELF = ROOT / 'manage.py'
RULES = ROOT / 'rules.nft'
UNIT = 'jumpserver-ssh-gate.service'
TIMER = 'jumpserver-ssh-rollback'
TEXT = '''table inet jms_ssh_gate {
 chain input {
  type filter hook input priority -20; policy accept;
  tcp dport 22 iifname "tailscale0" ip saddr 100.90.207.58 accept
  tcp dport 22 drop
 }
}
'''
def run(*args, check=True):
    return subprocess.run(args, check=check)
def exists():
    return subprocess.run(['/usr/sbin/nft','list','table','inet',TABLE],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode == 0
if os.geteuid() != 0:
    sys.exit('Run with sudo.')
mode = sys.argv[1] if len(sys.argv)>1 else 'apply'
if mode == 'rollback':
    run('systemctl','disable','--now',UNIT,check=False)
    if exists(): run('/usr/sbin/nft','delete','table','inet',TABLE)
    print('SSH_GATE_ROLLED_BACK: only SSH gate removed; RDP rules untouched.',flush=True)
elif mode == 'boot':
    if not exists(): run('/usr/sbin/nft','-f',str(RULES))
elif mode == 'apply':
    if exists(): sys.exit('SSH gate already exists. Verify access, then use confirm or rollback.')
    if '100.119.208.88/32' not in subprocess.check_output(['ip','-4','addr','show','tailscale0'],text=True):
        sys.exit('Unexpected host/Tailscale address; stopped.')
    ROOT.mkdir(mode=0o700,exist_ok=True)
    os.chmod(ROOT,0o700)
    if pathlib.Path(__file__).resolve()!=SELF:
        shutil.copyfile(__file__,SELF)
    os.chmod(SELF,0o700)
    RULES.write_text(TEXT)
    os.chmod(RULES,0o600)
    run('/usr/sbin/nft','--check','-f',str(RULES))
    run('systemd-run','--unit='+TIMER,'--on-active=10m','--timer-property=AccuracySec=1s','/usr/bin/python3',str(SELF),'rollback')
    print('Rollback armed for 10 minutes. Applying SSH gate...',flush=True)
    run('/usr/sbin/nft','-f',str(RULES))
    run('/usr/sbin/nft','list','table','inet',TABLE)
    print('SSH_GATE_TEMPORARY: test a NEW JumpServer SSH connection before confirm.',flush=True)
elif mode == 'confirm':
    if not exists(): sys.exit('No active SSH gate; apply and test again.')
    if subprocess.run(['systemctl','is-active','--quiet',TIMER+'.timer']).returncode:
        sys.exit('Rollback timer not active; inspect state before proceeding.')
    unit = pathlib.Path('/etc/systemd/system') / UNIT
    unit.write_text('''[Unit]
Description=Allow edge SSH only from JumpServer over Tailscale
Before=ssh.service ssh.socket
[Service]
Type=oneshot
ExecStart=/usr/bin/python3 /var/lib/jumpserver-ssh-gate/manage.py boot
RemainAfterExit=yes
[Install]
WantedBy=multi-user.target
''')
    os.chmod(unit,0o644)
    run('systemctl','daemon-reload')
    run('systemctl','enable','--now',UNIT)
    run('systemctl','stop',TIMER+'.timer')
    run('systemctl','is-enabled',UNIT)
    run('/usr/sbin/nft','list','table','inet',TABLE)
    print('SSH_GATE_PERSISTED: reboot recovery still requires separate verification.',flush=True)
else:
    sys.exit('Usage: apply | confirm | rollback')
