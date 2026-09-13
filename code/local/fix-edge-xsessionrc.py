#!/usr/bin/env python3
"""Replace the pilot account's incompatible inherited GNOME initialization only."""
import datetime,os,pwd,shutil,subprocess,sys
from pathlib import Path
if os.geteuid()!=0:sys.exit('Run with sudo python3')
u=pwd.getpwnam('jmsdesk')
assert u.pw_dir=='/home/jmsdesk','Unexpected account home'
assert Path('/var/lib/jumpserver-rdp-pilot/account-created').exists(),'Expected pilot marker missing'
p=Path(u.pw_dir)/'.xsessionrc'
assert p.is_file() and not p.is_symlink(),'Unexpected .xsessionrc file'
old=p.read_text()
marker='# JumpServer Xfce pilot: POSIX-compatible session environment.'
if marker not in old:
 assert 'remove_apps=(' in old,'File differs from diagnosed Jetson template; no changes made'
 backup=Path('/var/lib/jumpserver-rdp-pilot')/('xsessionrc-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S')+'.bak')
 shutil.copy2(p,backup);backup.chmod(0o600)
 print('Backup:',backup)
 data=marker+'\n# Desktop startup is handled by ~/.xsession; skip inherited GNOME customization.\n'
 temp=p.with_name('.xsessionrc.jumpserver-new')
 assert not temp.exists() and not temp.is_symlink(),'Temporary path exists'
 with temp.open('x') as f:
  os.fchmod(f.fileno(),0o600);f.write(data);f.flush();os.fsync(f.fileno())
 os.chown(temp,u.pw_uid,u.pw_gid)
 subprocess.run(['/bin/sh','-n',str(temp)],check=True)
 os.replace(temp,p)
subprocess.run(['/bin/sh','-n',str(p)],check=True)
subprocess.run(['/bin/sh','-n',str(Path(u.pw_dir)/'.xsession')],check=True)
print('XSESSIONRC_FIXED: shell syntax passed; close the old Web GUI tab and reconnect. Desktop/replay still require verification.')
