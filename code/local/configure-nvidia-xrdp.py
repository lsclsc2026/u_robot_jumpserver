#!/usr/bin/env python3
import os, re, subprocess, shutil, datetime
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

def main():
    if os.geteuid() != 0:
        raise SystemExit('Please run using sudo python3.')
    addresses = subprocess.check_output(['ip','-4','addr','show','tailscale0'], text=True)
    if '100.118.134.43/32' not in addresses:
        raise SystemExit('Unexpected machine: expected Tailscale IP 100.118.134.43. No changes.')
    spec = {
        Path('/etc/xrdp/xrdp.ini'): [('Globals', dict(port='tcp://100.118.134.43:3389',security_layer='tls',crypt_level='high'))],
        Path('/etc/xrdp/sesman.ini'): [('Globals', dict(EnableUserWindowManager='true',UserWindowManager='startwm.sh',DefaultWindowManager='startwm.sh')),
                                     ('Security', dict(AllowRootLogin='false',TerminalServerUsers='jms-rdp-users',AlwaysGroupCheck='true'))]
    }
    prepared = {}
    for p, edits in spec.items():
        if p.is_symlink() or not p.is_file():
            raise RuntimeError('Unexpected config path: ' + str(p))
        old = p.read_text()
        new = old
        for section, changes in edits:
            new = update(new, section, changes)
        params = lambda s: [l for l in s.splitlines() if re.match(r'^\s*param\s*=',l,re.I)]
        if params(old) != params(new):
            raise RuntimeError('param preservation failed')
        prepared[p] = new
    backup = Path('/var/backups') / ('jms-xrdp-' + datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
    backup.mkdir(mode=0o700)
    for p in prepared:
        shutil.copy2(p, backup / p.name)
    try:
        for p, new in prepared.items():
            p.write_text(new)
        for p, new in prepared.items():
            if p.read_text() != new:
                raise RuntimeError('Readback mismatch')
    except Exception:
        for p in prepared:
            shutil.copy2(backup / p.name, p)
        raise
    print('Backup:', backup)
    for p, edits in spec.items():
        print(p)
        for section, changes in edits:
            for key, value in changes.items():
                print(f'  [{section}] {key}={value}')
    print('XRDP_CONFIG_READY: param lines preserved; no services started or restarted.')

if __name__ == '__main__':
    main()
