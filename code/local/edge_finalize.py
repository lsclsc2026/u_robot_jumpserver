#!/usr/bin/env python3
"""Temporary SSH/RDP firewall, manual new-session verification, then NoMachine shutdown."""
import argparse, fcntl, ipaddress, json, os, shutil, subprocess, sys, time, uuid
from pathlib import Path
ROOT=Path('/var/lib/jms-edge-finalize')
TABLE='jms_edge_final'
UNIT='jms-edge-final.service'
SELF=ROOT/'manage.py'

def run(*args, capture=False, check=True):
    return subprocess.run(args,check=check,text=True,capture_output=capture,timeout=120)
def rules(ip):
    ip=str(ipaddress.IPv4Address(ip))
    return f'''table inet {TABLE} {{
 chain input {{
  type filter hook input priority -20; policy accept;
  tcp dport {{ 22, 3389 }} iifname "tailscale0" ip saddr {ip} accept
  tcp dport {{ 22, 3389 }} drop
 }}
}}
'''
def exists(table=TABLE):
    return run('/usr/sbin/nft','list','table','inet',table,capture=True,check=False).returncode==0

def manual_mode_ok(code, output):
    return code in (0, 1) and 'NX> 804 Startup of nxserver is: Manual.' in output.splitlines()

def nx_stopped(output):
    lines=set(line.strip() for line in output.splitlines())
    return all('NX> 162 Disabled service: '+name+'.' in lines for name in ('nxserver','nxnode','nxd'))

def stop_nomachine(nx):
    # Some NX versions return 1 even after successfully setting Manual.
    run(str(nx),'--shutdown',check=False)
    mode=run(str(nx),'--startmode','manual',capture=True,check=False)
    output=mode.stdout+mode.stderr
    print(output,flush=True)
    if not manual_mode_ok(mode.returncode, output):
        raise RuntimeError('NoMachine Manual startup was not confirmed; firewall remains pending.')
    state=run(str(nx),'--status',capture=True,check=False)
    output=state.stdout+state.stderr
    print(output,flush=True)
    if not nx_stopped(output):
        raise RuntimeError('NoMachine disabled services not confirmed; firewall remains pending.')

def load(): return json.loads((ROOT/'state.json').read_text())
def save(s):
    tmp=ROOT/'state.tmp';tmp.write_text(json.dumps(s,indent=2));tmp.chmod(0o600);os.replace(tmp,ROOT/'state.json')
def require_pending(s, now):
    if s.get('phase')!='pending' or s.get('deadline',0)-now<30:
        raise RuntimeError('No pending window with at least 30 seconds remaining; inspect status/reapply after rollback.')
def rollback(s):
    run('systemctl','disable','--now',UNIT,check=False)
    if exists(): run('/usr/sbin/nft','delete','table','inet',TABLE)
    s['phase']='rolled_back';save(s)
    print('FIREWALL_ROLLED_BACK: only this firewall removed. NoMachine is NOT automatically restarted.',flush=True)
def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['apply','confirm','status','rollback','boot','auto-rollback'])
    parser.add_argument('--bastion-ip')
    parser.add_argument('--minutes',type=int,default=15)
    a=parser.parse_args()
    if os.geteuid()!=0: raise RuntimeError('Use sudo python3.')
    if ROOT.is_symlink(): raise RuntimeError('Unexpected state symlink.')
    ROOT.mkdir(mode=0o700,exist_ok=True);ROOT.chmod(0o700)
    with (ROOT/'lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        if a.action=='apply':
            if not a.bastion_ip: raise RuntimeError('--bastion-ip required')
            text=rules(a.bastion_ip)
            if not 5<=a.minutes<=60: raise RuntimeError('Use 5..60 minutes.')
            for table in [TABLE,'jms_ssh_gate','jumpserver_rdp_pilot','jms_access_guard']:
                if exists(table): raise RuntimeError('Existing access table '+table+'; do not stack policies. Inspect existing deployment.')
            if (ROOT/'state.json').exists() and load().get('phase') in ['pending','committed']:
                raise RuntimeError('Existing transaction; use status/rollback first.')
            status=json.loads(run('tailscale','status','--json',capture=True).stdout)
            if status.get('BackendState')!='Running' or not any(a.bastion_ip in p.get('TailscaleIPs',[]) for p in (status.get('Peer') or {}).values()):
                raise RuntimeError('Tailscale/bastion visibility not ready.')
            if not sys.stdin.isatty(): raise RuntimeError('Run interactively from a JumpServer SSH session.')
            print('Only JumpServer will reach TCP 22/3389; LAN/direct Tailscale SSH will disconnect.\nUse JumpServer terminal. Confirm its SSH AND desktop already work. NoMachine remains running during trial.')
            if input('Type APPLY to start the timed firewall trial: ').strip()!='APPLY': return
            shutil.copyfile(Path(__file__),SELF) if Path(__file__).resolve()!=SELF else None
            SELF.chmod(0o700)
            (ROOT/'rules.nft').write_text(text);(ROOT/'rules.nft').chmod(0o600)
            run('/usr/sbin/nft','--check','-f',str(ROOT/'rules.nft'))
            timer='jms-edge-rollback-'+uuid.uuid4().hex[:12]
            s={'phase':'pending','bastion_ip':a.bastion_ip,'deadline':time.time()+60*a.minutes,'timer':timer}
            save(s)
            try:
                run('systemd-run','--unit='+timer,'--on-active='+str(a.minutes)+'m','--timer-property=AccuracySec=1s','/usr/bin/python3',str(SELF),'auto-rollback')
                run('systemctl','is-active','--quiet',timer+'.timer')
                run('/usr/sbin/nft','-f',str(ROOT/'rules.nft'))
            except Exception:
                rollback(s);raise
            print('FIREWALL_TRIAL_ACTIVE: automatic rollback in '+str(a.minutes)+' minutes.\nOpen NEW JumpServer SSH and desktop; test direct LAN SSH/RDP failure.\nThen in NEW JumpServer SSH run:\nsudo python3 /var/lib/jms-edge-finalize/manage.py confirm',flush=True)
        elif a.action=='status':
            if not (ROOT/'state.json').exists(): print('Not configured');return
            s=load();print(json.dumps(s,indent=2));print('table_present:',exists())
            run('systemctl','is-enabled',UNIT,check=False)
            if s.get('timer'): run('systemctl','is-active',s['timer']+'.timer',check=False)
        elif a.action=='boot':
            s=load()
            if s.get('phase')!='committed': raise RuntimeError('Not committed; refusing persistent load.')
            if not exists(): run('/usr/sbin/nft','-f',str(ROOT/'rules.nft'))
        elif a.action in ['rollback','auto-rollback']:
            s=load()
            if a.action=='auto-rollback' and s.get('phase')!='pending': return
            rollback(s)
        elif a.action=='confirm':
            s=load();require_pending(s,time.time())
            if not exists(): raise RuntimeError('Firewall table is missing.')
            run('systemctl','is-active','--quiet',s['timer']+'.timer')
            if not sys.stdin.isatty(): raise RuntimeError('Interactive confirmation required.')
            print('Confirm: NEW JumpServer SSH + desktop work; LAN direct SSH/RDP fail.\nNext: disconnect NoMachine users, disable NoMachine auto-start, persist firewall.\nFirewall rollback does not restart NoMachine.')
            if input('After testing, type VERIFIED: ').strip()!='VERIFIED': return
            require_pending(s,time.time())
            nx=next((Path(p) for p in ['/usr/NX/bin/nxserver','/etc/NX/nxserver'] if Path(p).is_file()),None)
            if nx:
                cfg=Path('/usr/NX/etc/server.cfg')
                if cfg.is_file() and not (ROOT/'nomachine-server.cfg.before').exists(): shutil.copy2(cfg,ROOT/'nomachine-server.cfg.before')
                stop_nomachine(nx)
                if run('pgrep','-x','nxd',capture=True,check=False).returncode==0:
                    raise RuntimeError('NoMachine nxd still running; firewall trial remains pending. Inspect service.')
                s['nomachine']='shutdown_manual_requested'
            else:
                if run('pgrep','-x','nxd',capture=True,check=False).returncode==0:
                    raise RuntimeError('nxd found but nxserver path unknown; stop and inspect.')
                s['nomachine']='not_found_at_standard_paths'
            require_pending(s,time.time())
            path=Path('/etc/systemd/system')/UNIT
            if path.is_symlink(): raise RuntimeError('Unexpected unit symlink.')
            path.write_text('''[Unit]
Description=JumpServer-only SSH/RDP ingress
Before=ssh.service ssh.socket xrdp.service
[Service]
Type=oneshot
ExecStart=/usr/bin/python3 /var/lib/jms-edge-finalize/manage.py boot
RemainAfterExit=yes
[Install]
WantedBy=multi-user.target
''')
            # Enable for next boot without launching boot helper while holding its lock.
            run('systemctl','daemon-reload')
            run('systemctl','enable',UNIT)
            s['phase']='committed';save(s)
            run('systemctl','stop',s['timer']+'.timer')
            print('FINALIZATION_COMMITTED: firewall exists and boot unit enabled.\nReboot, real NoMachine connection failure, and other remote tools still require acceptance testing.')
if __name__=='__main__':
    try: main()
    except Exception as e:
        print('FINALIZATION_STOPPED: '+str(e),file=sys.stderr);sys.exit(1)
