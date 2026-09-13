#!/usr/bin/env python3
"""Add the approved wired LAN HTTPS endpoint; run locally with sudo.
No account/password changes. Back up changed files and roll back on failure.
"""
import datetime
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import socket
import threading

LAN = '172.18.14.69'
TAIL = '100.90.207.58'
HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('installer', HERE / 'install-jumpserver-laptop.py')
b = importlib.util.module_from_spec(spec)
spec.loader.exec_module(b)


def update_domains(source):
    matches = list(re.finditer(r'^DOMAINS=(.*)$', source, re.M))
    b.require(len(matches) == 1, 'Expected exactly one DOMAINS setting')
    value = matches[0].group(1).strip().strip('\"\'')
    domains = [v.strip() for v in value.split(',') if v.strip()]
    b.require('*' not in domains, 'Unexpected wildcard DOMAINS')
    for host in (TAIL, LAN):
        if host not in domains and host + ':443' not in domains:
            domains.append(host + ':443')
    return source[:matches[0].start()] + 'DOMAINS=' + ','.join(domains) + source[matches[0].end():]


def update_override(source):
    block = '    ports:\n      - "' + LAN + ':80:80"\n      - "' + LAN + ':443:443"\n'
    if block in source:
        return source
    b.require(source.count('  web:\n') == 1, 'Expected one web override')
    web = re.split(r'\n  [^ ]', source.split('  web:\n', 1)[1], maxsplit=1)[0]
    b.require('    ports:' not in web and LAN not in source, 'Unexpected existing LAN/port override')
    return source.replace('  web:\n', '  web:\n' + block, 1)


def port_set(config):
    return {(service, p.get('host_ip', '0.0.0.0'), str(p['published']),
             str(p['target']), p.get('protocol', 'tcp'))
            for service, settings in config['services'].items()
            for p in settings.get('ports', [])}


def main():
    b.require(os.geteuid() == 0, 'Run with sudo python3')
    b.require(socket.gethostname() == 'ksxq-Jiaolong15K-Series-GM5XGEE', 'Unexpected host')
    os.umask(0o077)
    lock = open('/run/lock/jumpserver-laptop-install.lock', 'w')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    backup = b.STATE / ('lan-backup-' + stamp)
    backup.mkdir(mode=0o700)
    b.LOG = (backup / 'operation.log').open('x', buffering=1)
    print('Protected backup and log: ' + str(backup), flush=True)
    stop = threading.Event()
    def heartbeat():
        while not stop.wait(20):
            print('LAN configuration is running; keep this terminal open.', flush=True)
    threading.Thread(target=heartbeat, daemon=True).start()
    config = b.CONFIG / 'config.txt'
    override = b.INSTALLER / 'compose/laptop.yml'
    certificate = b.CONFIG / 'nginx/cert/server.crt'
    files = [config, override, certificate]
    saved = []
    changed = False
    try:
        print('1/5 Verify wired address, existing endpoints and save configuration', flush=True)
        addresses = json.loads(b.run(['ip', '-j', '-4', 'address', 'show', 'dev', 'enp2s0'], capture=True))
        b.require(any(a.get('local') == LAN for i in addresses for a in i['addr_info']), 'Expected LAN IP is not assigned')
        before = json.loads(b.compose('config', '--format', 'json', capture=True))
        old_ports = port_set(before)
        required = {('web', TAIL, '80', '80', 'tcp'), ('web', TAIL, '443', '443', 'tcp'),
                    ('koko', '127.0.0.1', '2222', '2222', 'tcp')}
        extra = {('web', LAN, '80', '80', 'tcp'), ('web', LAN, '443', '443', 'tcp')}
        b.require(required <= old_ports and old_ports <= required | extra, 'Unexpected exposed ports; no changes made')
        for port in (80, 443):
            if ('web', LAN, str(port), str(port), 'tcp') not in old_ports:
                with socket.socket() as s:
                    s.bind((LAN, port))
        new_config = update_domains(config.read_text())
        new_override = update_override(override.read_text())
        for i, path in enumerate(files):
            b.require(path.is_file() and not path.is_symlink(), 'Unexpected configuration path')
            dest = backup / (str(i) + '-' + path.name)
            shutil.copy2(path, dest)
            saved.append((path, dest))
        (backup / 'restore-map.json').write_text(json.dumps([(str(p), str(d)) for p, d in saved]))
        print('2/5 Prepare a certificate for both IP addresses', flush=True)
        cert_text = b.run(['openssl', 'x509', '-in', certificate, '-noout', '-ext', 'subjectAltName'], capture=True)
        new_cert = backup / 'new-server.crt'
        if all('IP Address:' + ip in cert_text for ip in (LAN, TAIL)):
            shutil.copy2(certificate, new_cert)
        else:
            b.run(['openssl', 'req', '-x509', '-new', '-sha256', '-days', '365',
                   '-key', b.CONFIG / 'nginx/cert/server.key', '-out', new_cert,
                   '-subj', '/CN=' + TAIL, '-addext', 'subjectAltName=IP:' + TAIL + ',IP:' + LAN])
        for ip in (LAN, TAIL):
            b.run(['openssl', 'verify', '-CAfile', new_cert, '-verify_ip', ip, new_cert])
        print('3/5 Add LAN ports and allowed domain; validate merged Compose', flush=True)
        changed = True
        b.write_private(config, new_config)
        b.write_private(override, new_override)
        b.write_private(certificate, new_cert.read_text())
        after = json.loads(b.compose('config', '--format', 'json', capture=True))
        b.require(port_set(after) == old_ports | extra, 'Port validation failed')
        for service in ('core', 'celery'):
            env = after['services'][service].get('environment', {})
            b.require(LAN + ':443' in env.get('DOMAINS', '') or LAN in env.get('DOMAINS', '').split(','),
                      'Allowed domain not passed to ' + service)
        print('4/5 Recreate application services with offline images; wait for health', flush=True)
        b.compose('up', '-d', '--no-deps', '--pull', 'never', '--force-recreate', '--wait',
                  '--wait-timeout', '600', 'core', 'celery', 'koko', 'web', timeout=720)
        print('5/5 Verify HTTPS and HTTP redirect on both addresses', flush=True)
        for ip in (LAN, TAIL):
            for path in ('/core/auth/login/', '/api/health/'):
                code = b.run(['curl', '--noproxy', '*', '--connect-timeout', '5', '--max-time', '20',
                              '--cacert', certificate, '-sS', '-o', '/dev/null', '-w', '%{http_code}',
                              'https://' + ip + path], capture=True)
                b.require(code == '200', 'HTTPS verification failed: ' + ip + path)
            redirect = b.run(['curl', '--noproxy', '*', '-sS', '--max-time', '10', '-o', '/dev/null',
                              '-w', '%{http_code} %{redirect_url}', 'http://' + ip + '/'], capture=True)
            b.require(redirect == '307 https://' + ip + '/', 'Unexpected HTTP redirect')
            print('HTTPS_LOGIN_AND_HEALTH_OK: https://' + ip, flush=True)
        owner = pwd.getpwnam('ksxq')
        b.write_private(b.STAGING / 'jumpserver-server.crt', certificate.read_text(), owner)
        fingerprint = b.run(['openssl', 'x509', '-in', certificate, '-noout', '-fingerprint', '-sha256'], capture=True)
        report = {'lan_url': 'https://' + LAN, 'tailscale_url': 'https://' + TAIL,
                  'verified_at': datetime.datetime.now().isoformat(), 'certificate': fingerprint,
                  'server_checks_passed': True, 'wifi_client_test_pending': True,
                  'dhcp_reservation_pending': True, 'backup': str(backup)}
        b.write_private(b.STAGING / 'lan-access-status.json', json.dumps(report, indent=2) + '\n', owner)
        print(fingerprint, flush=True)
        print('LAN_SERVER_CONFIGURATION_COMPLETE: Wi-Fi browser login and Web CLI test pending.', flush=True)
    except Exception as exc:
        print('LAN_CONFIGURATION_STOPPED: ' + str(exc), flush=True)
        if changed:
            try:
                for path, dest in saved:
                    shutil.copy2(dest, path)
                b.compose('up', '-d', '--no-deps', '--pull', 'never', '--force-recreate', '--wait',
                          '--wait-timeout', '600', 'core', 'celery', 'koko', 'web', timeout=720)
                print('Previous configuration restored; verify existing Tailscale access.', flush=True)
            except Exception:
                print('ROLLBACK_NEEDS_ATTENTION: inspect protected operation.log in ' + str(backup), flush=True)
        raise SystemExit(1)
    finally:
        stop.set()
        b.LOG.close()

if __name__ == '__main__':
    main()
