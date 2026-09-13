#!/usr/bin/env python3
"""Switch only the pilot user's next RDP session; --restore restores Xfce launcher."""
import os,pwd,shutil,subprocess,sys
from pathlib import Path
SESSION='''#!/bin/sh
# JumpServer Ubuntu/GNOME independent Xorg desktop
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
if __name__=='__main__':
 if os.geteuid()!=0:sys.exit('Run with sudo python3')
 assert sys.argv[1:] in ([],['--restore']),'Only --restore is supported'
 u=pwd.getpwnam('jmsdesk');assert u.pw_dir=='/home/jmsdesk'
 state=Path('/var/lib/jumpserver-rdp-pilot');assert (state/'account-created').exists()
 p=Path(u.pw_dir)/'.xsession';assert p.is_file() and not p.is_symlink()
 backup=state/'xsession-before-ubuntu';assert not backup.is_symlink()
 if sys.argv[1:]==['--restore']:
  assert backup.is_file(),'Backup missing'
  data=backup.read_text()
 else:
  for required in ['/usr/bin/gnome-session','/usr/bin/gnome-shell','/usr/share/gnome-session/sessions/ubuntu.session']:
   assert Path(required).is_file(),'Missing '+required
  subprocess.run(['/bin/sh','-n',str(Path(u.pw_dir)/'.xsessionrc')],check=True)
  if not backup.exists():
   assert 'startxfce4' in p.read_text(),'Expected working Xfce launcher'
   shutil.copy2(p,backup);backup.chmod(0o600)
  data=SESSION
 tmp=p.with_name('.xsession.switch-tmp');assert not tmp.exists() and not tmp.is_symlink()
 with tmp.open('x') as f:
  os.fchmod(f.fileno(),0o700);f.write(data);f.flush();os.fsync(f.fileno())
 os.chown(tmp,u.pw_uid,u.pw_gid)
 subprocess.run(['/bin/sh','-n',str(tmp)],check=True)
 os.replace(tmp,p)
 print('XFCE_RESTORED' if sys.argv[1:] else 'UBUNTU_SESSION_CONFIGURED')
 print('Changes apply on a NEW desktop login. Log out inside the old desktop; closing browser alone may preserve the session.')
 print('Desktop compatibility and recording still require a real connection test.')
