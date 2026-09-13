#!/usr/bin/env python3
"""Restore intact packaged INI backups; preserve repeated param= entries."""
import datetime,os,re,shutil,subprocess,sys
from pathlib import Path

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
 assert '100.119.208.88' in subprocess.check_output(['ip','-4','addr','show','tailscale0'],text=True),'Unexpected host'
 state=Path('/var/lib/jumpserver-rdp-pilot')
 candidates=[]
 for p in sorted(state.glob('sesman.ini.*')):
  s=p.read_text();m=re.search(r'(?ms)^\[Xorg\].*?(?=^\[|\Z)',s)
  if m and len(re.findall(r'^param=',m.group(),re.M))>=6 and '/usr/lib/xorg/Xorg' in m.group():candidates.append(p)
 if not candidates:raise RuntimeError('No intact pre-installation sesman backup; nothing changed')
 source=candidates[0];stamp=source.name.removeprefix('sesman.ini.')
 xsource=state/('xrdp.ini.'+stamp)
 assert xsource.is_file(),'Matching xrdp.ini backup missing'
 sesman=update_section(source.read_text(),'Security',{'AllowRootLogin':'false','TerminalServerUsers':'jms-rdp-users','AlwaysGroupCheck':'true'})
 xrdp=update_section(xsource.read_text(),'Globals',{'port':'tcp://100.119.208.88:3389','security_layer':'tls','crypt_level':'high'})
 assert re.findall(r'^param=.*$',sesman,re.M)==re.findall(r'^param=.*$',source.read_text(),re.M),'Parameter preservation failed'
 backup=state/('parameter-fix-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S'));backup.mkdir(mode=0o700)
 print('Intact configuration source:',source)
 print('Backup:',backup)
 subprocess.run(['systemctl','stop','xrdp','xrdp-sesman'],check=True)
 for name,data in [('sesman.ini',sesman),('xrdp.ini',xrdp)]:
  p=Path('/etc/xrdp')/name;shutil.copy2(p,backup/name);p.write_text(data)
 subprocess.run(['systemctl','start','xrdp'],check=True)
 subprocess.run(['systemctl','is-active','xrdp','xrdp-sesman'],check=True)
 block=re.search(r'(?ms)^\[Xorg\].*?(?=^\[|\Z)',sesman).group()
 print(block)
 print('RDP_PARAMETERS_RESTORED: reconnect Web GUI to verify actual desktop startup.')
if __name__=='__main__':main()
