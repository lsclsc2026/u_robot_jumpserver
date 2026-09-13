#!/usr/bin/env python3
"""Install the agreed single-laptop JumpServer deployment from verified offline files.

Run with sudo on ksxq-Jiaolong15K-Series-GM5XGEE. Never prints credentials.
Uses official v4.10.19 Compose templates; no formatting or daemon reconfiguration.
"""
import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import pwd
import secrets
import shutil
import socket
import string
import subprocess
import sys
import tarfile
import threading

VERSION = 'v4.10.19'
IP = '100.90.207.58'
HOME_DIR = Path('/home/ksxq')
STAGING = HOME_DIR / 'jumpserver-deployment'
INSTALLER = Path('/opt/jumpserver-installer-' + VERSION)
CONFIG = Path('/opt/jumpserver/config')
DATA = Path('/data/jumpserver')
STATE = Path('/var/lib/jumpserver-laptop-setup')
ARCHIVE_SHA = '3a9fb2b5256ae60eff1340cca24a1546157d09fc174244b342f38e4117418f87'
EXPECTED_IMAGES = {
    'jumpserver/core:v4.10.19-ce': 'sha256:fe7143586510e49831504e0fbfae16d4e06784c52740ec0310e9598d3585602a',
    'jumpserver/koko:v4.10.19-ce': 'sha256:ca11adce835946e0a79713de5f896901b6775d2a11870dfe2cc373509e8f1679',
    'jumpserver/web:v4.10.19-ce': 'sha256:58c32889986d955c03328ceac1fce95fdc307fe6dcd76eb9c66c228901fa5d29',
    'postgres:16.15-bookworm': 'sha256:5f71c21b69a7977b82247582e2e731ed76bdebaadb7dd7945ed76bcc9ed06632',
    'redis:7.4.10-bookworm': 'sha256:c0a0a2a0551f7339f9509d1f9d80296b5486e2d86d1265ae29bec790f1ab4701',
}
SERVICES = ['core', 'celery', 'koko', 'web', 'postgresql', 'redis']
COMPOSE_FILES = ['network', *SERVICES, 'web.https', 'web.http', 'laptop']
LOG = None


def say(message):
    print(datetime.datetime.now().isoformat(timespec='seconds'), message, flush=True)


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def write_private(path, content, owner=None):
    require(not path.is_symlink(), 'Refusing symlink: ' + str(path))
    tmp = path.with_name(path.name + '.tmp-' + secrets.token_hex(4))
    with tmp.open('x') as stream:
        os.chmod(tmp, 0o600)
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    if owner:
        os.chown(tmp, owner.pw_uid, owner.pw_gid)
    os.replace(tmp, path)


def run(args, *, capture=False, input_text=None, timeout=900, cwd=None, env=None):
    # Protect subprocess diagnostics: database tools and Django can include secrets.
    result = subprocess.run([str(arg) for arg in args], input=input_text, text=True,
                            stdout=subprocess.PIPE if capture else LOG,
                            stderr=LOG, timeout=timeout, cwd=cwd, env=env)
    require(result.returncode == 0, 'Command failed: ' + str(args[0]) + '; see protected install log')
    return result.stdout.strip() if capture else None


def inspect_image(reference):
    result = subprocess.run(['docker', 'image', 'inspect', reference],
                            stdout=subprocess.PIPE, stderr=LOG, text=True, timeout=60)
    if result.returncode:
        return None
    return json.loads(result.stdout)[0]


def verify_loaded_image(actual, expected):
    # containerd uses a manifest/index ID, while the classic store uses a config
    # digest. Compare the verified image's content instead of conflating those IDs.
    require(actual is not None, 'Loaded image cannot be inspected by its tag')
    require(actual.get('Architecture') == expected['architecture'] == 'amd64'
            and actual.get('Os') == expected['os'] == 'linux', 'Loaded image platform mismatch')
    require(actual.get('RootFS', {}).get('Type') == expected['rootfs']['type']
            and actual.get('RootFS', {}).get('Layers') == expected['rootfs']['diff_ids'],
            'Loaded image filesystem layer mismatch')
    # Docker's API may add omitted zero-valued legacy fields. Keep all nonempty
    # config fields, including nested port/volume mappings and ordered env/cmd lists.
    def meaningful(config):
        return {k: v for k, v in config.items() if v not in (None, '', False, [], {})}
    require(meaningful(actual.get('Config') or {}) == meaningful(expected['config']),
            'Loaded image runtime configuration mismatch')


def load_offline_image(item):
    archive_path = STAGING / 'images' / item['file']
    with tarfile.open(archive_path) as archive:
        manifests = json.load(archive.extractfile('manifest.json'))
        require(len(manifests) == 1, 'Expected a single-platform image archive')
        manifest = manifests[0]
        config_bytes = archive.extractfile(manifest['Config']).read()
        require('sha256:' + hashlib.sha256(config_bytes).hexdigest() == EXPECTED_IMAGES[item['ref']],
                'Archived image configuration digest mismatch')
        expected = json.loads(config_bytes)
        tags = manifest.get('RepoTags') or []
        require(len(tags) == 1, 'Expected one source tag in image archive')
        source_tag = tags[0].removeprefix('index.docker.io/').removeprefix('library/')
    actual = inspect_image(source_tag)
    if actual is None:
        say('Loading ' + item['ref'])
        run(['docker', 'load', '-i', archive_path])
        actual = inspect_image(source_tag)
    verify_loaded_image(actual, expected)
    run(['docker', 'tag', source_tag, item['ref']])
    verify_loaded_image(inspect_image(item['ref']), expected)
    say('Verified image content and tag: ' + item['ref'])
    return item['ref']


def environment():
    env = os.environ.copy()
    # Config and version must come from this deployment, not caller overrides.
    env.update(VERSION=VERSION + '-ce', NAMESPACE='jumpserver', CONFIG_DIR=str(CONFIG),
               CONFIG_FILE=str(CONFIG / 'config.txt'), CONFIG_SAFE_FILE=str(CONFIG / 'config_safe.txt'),
               COMPOSE_PROJECT_NAME='jms', JS_CONFIG_DIR=str(CONFIG))
    return env


def compose(*args, bootstrap=False, capture=False, timeout=900):
    command = ['docker', 'compose', '--env-file', CONFIG / 'config.txt', '-p', 'jms']
    for name in COMPOSE_FILES + (['laptop-bootstrap'] if bootstrap else []):
        command += ['-f', INSTALLER / 'compose' / (name + '.yml')]
    return run(command + list(args), capture=capture, cwd=INSTALLER,
               env=environment(), timeout=timeout)


def public_config():
    return {
        'VOLUME_DIR': str(DATA), 'DOCKER_SUBNET': '192.168.250.0/24',
        'USE_IPV6': '0', 'USE_XPACK': '0', 'IMAGE_PULL_POLICY': 'IfNotPresent',
        'DB_ENGINE': 'postgresql', 'DB_HOST': 'postgresql', 'DB_PORT': '5432',
        'DB_USER': 'postgres', 'DB_NAME': 'jumpserver',
        'REDIS_HOST': 'redis', 'REDIS_PORT': '6379',
        'HTTP_PORT': IP + ':80', 'HTTPS_PORT': IP + ':443',
        'SERVER_NAME': IP, 'DOMAINS': IP + ':443',
        'SSL_CERTIFICATE': 'server.crt', 'SSL_CERTIFICATE_KEY': 'server.key',
        'KOKO_SSH_PORT': '127.0.0.1:2222', 'CORE_HOST': 'http://core:8080',
        'CORE_ENABLED': '1', 'CELERY_ENABLED': '1', 'KOKO_ENABLED': '1', 'WEB_ENABLED': '1',
        'LION_ENABLED': '0', 'CHEN_ENABLED': '0', 'KOTL_ENABLED': '0', 'RAZOR_ENABLED': '0',
        'VAULT_ENABLED': 'false', 'PERIOD_TASK_ENABLED': 'true',
        'USE_LB': '1', 'TZ': 'Asia/Shanghai', 'LANGUAGE_CODE': 'zh',
        'LOG_LEVEL': 'ERROR', 'CLIENT_MAX_BODY_SIZE': '1024m',
        'SESSION_EXPIRE_AT_BROWSER_CLOSE': 'true', 'SESSION_COOKIE_SECURE': 'true',
        'CSRF_COOKIE_SECURE': 'true', 'TERMINAL_SESSION_KEEP_DURATION': '30',
        'LOGIN_LOG_KEEP_DAYS': '90', 'OPERATE_LOG_KEEP_DAYS': '90',
        'FTP_LOG_KEEP_DAYS': '90', 'TASK_LOG_KEEP_DAYS': '30',
        'SERVER_HOSTNAME': 'ksxq-Jiaolong15K-Series-GM5XGEE', 'CURRENT_VERSION': VERSION + '-ce',
    }


def compose_override():
    return 'services:\n' + ''.join(
        f'  {name}:\n' + ('    environment:\n      HTTPS_PORT: "443"\n'
                          '    healthcheck:\n      test: ["CMD", "check", "http://localhost:51980/web/health/"]\n'
                          if name == 'web' else '') +
        '    pull_policy: never\n    logging:\n      driver: json-file\n      options:\n        max-size: "10m"\n        max-file: "3"\n'
        for name in SERVICES)


def main():
    global LOG
    require(os.geteuid() == 0, 'Run: sudo python3 /home/ksxq/install-jumpserver-laptop.py')
    os.umask(0o077)
    lock = open('/run/lock/jumpserver-laptop-install.lock', 'w')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    require(socket.gethostname() == 'ksxq-Jiaolong15K-Series-GM5XGEE', 'Unexpected host')
    owner = pwd.getpwnam('ksxq')
    STATE.mkdir(mode=0o700, exist_ok=True)
    log_path = STATE / ('install-' + datetime.datetime.now().strftime('%Y%m%d-%H%M%S') + '.log')
    LOG = log_path.open('x', buffering=1)
    say('Protected detailed log: ' + str(log_path))
    stop_heartbeat = threading.Event()
    def heartbeat():
        while not stop_heartbeat.wait(30):
            say('Installation is still running; please keep this terminal open.')
    threading.Thread(target=heartbeat, daemon=True).start()
    try:
        say('1/7 Verify host, storage and offline artifacts')
        run(['docker', 'info'], capture=True)
        run(['docker', 'compose', 'version'])
        ips = run(['tailscale', 'ip', '-4'], capture=True)
        require(IP in ips.splitlines(), 'Expected Tailscale IP is missing')
        fs = run(['findmnt', '-n', '-o', 'FSTYPE', '-T', '/data' if Path('/data').exists() else '/'], capture=True)
        require(fs == 'ext4', '/data must reside on the internal ext4 filesystem')
        require(shutil.disk_usage('/').free > 20 * 1024**3, 'Need at least 20 GiB free on internal SSD')
        mounted = run(['findmnt', '-n', '-o', 'UUID', '-M', '/srv/jumpserver-archive'], capture=True)
        require(mounted == '6AFB-F1FF', 'Expected USB archive mount is missing')
        archive = STAGING / 'downloads' / ('jumpserver-installer-' + VERSION + '.tar.gz')
        require(digest(archive) == ARCHIVE_SHA, 'Installer archive checksum mismatch')
        images = json.loads((STAGING / 'images/manifest.json').read_text())
        require(len(images) == 5 and {x['ref'] for x in images} == set(EXPECTED_IMAGES), 'Unexpected image list')
        for item in images:
            require(item['image_id'] == EXPECTED_IMAGES[item['ref']], 'Unexpected official image ID')
            require(Path(item['file']).name == item['file'], 'Invalid image archive filename')
            require(digest(STAGING / 'images' / item['file']) == item['sha256'], 'Image archive checksum mismatch')
        marker = STATE / 'managed-v4.10.19'
        first_run = not marker.exists()
        if first_run:
            require(not CONFIG.exists() and not INSTALLER.exists(), 'Existing JumpServer installation needs review')
            require(not DATA.exists() or not any(DATA.iterdir()), 'Existing JumpServer data needs review')
            require(not run(['docker', 'ps', '-aq'], capture=True), 'Existing containers need review')
            for bind_ip, port in [(IP, 80), (IP, 443), ('127.0.0.1', 2222)]:
                with socket.socket() as sock:
                    sock.bind((bind_ip, port))
            write_private(marker, 'Managed local deployment ' + VERSION + '\n')

        say('2/7 Load five verified official images; no online pull')
        for item in images:
            load_offline_image(item)

        say('3/7 Prepare official Compose templates and private configuration')
        if not INSTALLER.exists():
            with tarfile.open(archive) as source:
                for member in source.getmembers():
                    parts = Path(member.name).parts
                    require(parts and parts[0] == INSTALLER.name and '..' not in parts
                            and not member.name.startswith('/') and (member.isfile() or member.isdir()),
                            'Unsafe installer archive member')
                source.extractall('/opt')
            for directory, dirs, files in os.walk(INSTALLER):
                os.chown(directory, 0, 0)
                for filename in files:
                    os.chown(Path(directory) / filename, 0, 0)
        CONFIG.mkdir(parents=True, mode=0o700, exist_ok=True)
        DATA.mkdir(parents=True, mode=0o700, exist_ok=True)
        cfg_path = CONFIG / 'config.txt'
        if not cfg_path.exists():
            cfg = public_config()
            for key in ['SECRET_KEY', 'BOOTSTRAP_TOKEN', 'DB_PASSWORD', 'REDIS_PASSWORD']:
                cfg[key] = ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(48))
            write_private(cfg_path, '\n'.join(f'{k}={v}' for k, v in cfg.items()) + '\n')
        require(not DATA.is_symlink() and not CONFIG.is_symlink(), 'Data/config directory must not be a symlink')
        require(run(['findmnt', '-n', '-o', 'FSTYPE', '-T', DATA], capture=True) == 'ext4',
                'JumpServer runtime data must stay on ext4')
        cert_dir = CONFIG / 'nginx/cert'
        cert_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
        if not (cert_dir / 'server.crt').exists() and not (cert_dir / 'server.key').exists():
            run(['openssl', 'req', '-x509', '-nodes', '-newkey', 'rsa:3072', '-sha256', '-days', '365',
                 '-keyout', cert_dir / 'server.key', '-out', cert_dir / 'server.crt',
                 '-subj', '/CN=' + IP, '-addext', 'subjectAltName=IP:' + IP])
        require((cert_dir / 'server.crt').is_file() and (cert_dir / 'server.key').is_file(), 'Incomplete TLS certificate pair')
        run(['bash', INSTALLER / 'jmsctl.sh', 'config', 'init'], cwd=INSTALLER, env=environment())
        nginx_conf = CONFIG / 'nginx/lb_http_server.conf'
        nginx_text = nginx_conf.read_text()
        if 'proxy_set_header X-Forwarded-Proto ' not in nginx_text:
            nginx_text = nginx_text.replace('    proxy_set_header Host $host;',
                '    proxy_set_header Host $host;\n    proxy_set_header X-Forwarded-Proto $scheme;')
            write_private(nginx_conf, nginx_text)
        # App is IPv4 tailnet-only; no host database ports. Bound container log files.
        write_private(INSTALLER / 'compose/laptop.yml', compose_override())
        write_private(INSTALLER / 'compose/laptop-bootstrap.yml',
                      'services:\n  core:\n    command: sleep\n    healthcheck:\n      disable: true\n')
        rendered = json.loads(compose('config', '--format', 'json', capture=True))
        require(set(rendered['services']) == set(SERVICES), 'Unexpected enabled services')
        expected_ports = {('web', IP, '80', 80), ('web', IP, '443', 443),
                          ('koko', '127.0.0.1', '2222', 2222)}
        actual_ports = set()
        for name, service in rendered['services'].items():
            for port in service.get('ports', []):
                actual_ports.add((name, port['host_ip'], str(port['published']), port['target']))
            require(service['image'] in EXPECTED_IMAGES, 'Unexpected Compose image')
            require(service['logging']['options']['max-size'] == '10m', 'Unbounded Docker logs')
        require(actual_ports == expected_ports, 'Unexpected exposed ports')
        wrapper = '#!/usr/bin/env bash\nset -euo pipefail\ncd ' + str(INSTALLER) + '\n'
        wrapper += 'exec ./jmsctl.sh raw -f compose/laptop.yml "$@"\n'
        write_private(Path('/usr/local/sbin/jumpserver-laptop-compose'), wrapper)
        os.chmod('/usr/local/sbin/jumpserver-laptop-compose', 0o700)
        public_cert = STAGING / 'jumpserver-server.crt'
        write_private(public_cert, (cert_dir / 'server.crt').read_text(), owner)

        say('4/7 Start PostgreSQL and Redis; initialize database')
        compose('up', '-d', '--pull', 'never', '--wait', '--wait-timeout', '180', 'postgresql', 'redis')
        if not (STATE / 'database-initialized').exists():
            compose('up', '-d', '--pull', 'never', 'core', bootstrap=True)
            run(['docker', 'exec', '-i', 'jms_core', 'bash', '-c', './jms upgrade_db'], timeout=1200)
            write_private(STATE / 'database-initialized', 'Migration command succeeded\n')

        say('5/7 Set a unique initial administrator password before opening Web')
        if not (STATE / 'admin-initialized').exists():
            password_file = STATE / 'admin-bootstrap-password'
            if not password_file.exists():
                write_private(password_file, 'Js9!' + secrets.token_urlsafe(24) + '\n')
            password = password_file.read_text().strip()
            code = ('from users.models import User\n'
                    "u = User.objects.get(username='admin')\n"
                    'u.set_password(' + repr(password) + ')\n'
                    'u.need_update_password = True\nu.save()\n'
                    'u.refresh_from_db()\nassert u.check_password(' + repr(password) + ')\n'
                    "print('ADMIN_INITIALIZATION_VERIFIED')\n")
            output = run(['docker', 'exec', '-i', 'jms_core', 'python', '/opt/jumpserver/apps/manage.py',
                          'shell', '-i', 'python'], input_text=code, capture=True)
            require('ADMIN_INITIALIZATION_VERIFIED' in output, 'Admin verification marker missing')
            write_private(STAGING / 'admin-initial-password.txt', password + '\n', owner)
            write_private(STATE / 'admin-initialized', 'Initial credential verified before Web start\n')
        say('Credential file (read locally only): ' + str(STAGING / 'admin-initial-password.txt'))

        say('6/7 Start six services and wait for health checks')
        compose('up', '-d', '--pull', 'never', '--wait', '--wait-timeout', '600', timeout=720)
        health = []
        for name in SERVICES:
            info = json.loads(run(['docker', 'inspect', 'jms_' + name], capture=True))[0]
            status = info['State'].get('Health', {}).get('Status')
            require(info['State']['Running'] and status == 'healthy', name + ' is not healthy')
            require(info['HostConfig']['RestartPolicy']['Name'] == 'always', name + ' restart policy mismatch')
            health.append({'service': name, 'health': status,
                           'ports': info['HostConfig']['PortBindings'],
                           'restart_policy': info['HostConfig']['RestartPolicy']['Name']})
        say('7/7 Verify HTTPS using the generated certificate and publish safe status')
        response_code = run(['curl', '--silent', '--show-error', '--noproxy', '*', '--max-time', '30',
                             '--cacert', cert_dir / 'server.crt', '-o', '/dev/null', '-w', '%{http_code}',
                             'https://' + IP + '/core/auth/login/'], capture=True)
        require(response_code in ['200', '301', '302'], 'HTTPS login endpoint returned ' + response_code)
        report = {'version': VERSION + '-ce', 'url': 'https://' + IP,
                  'services': health, 'https_code': response_code, 'data_dir': str(DATA),
                  'archive_dir': '/srv/jumpserver-archive', 'archive_jobs_installed': False,
                  'edge_machine_connected': False, 'reboot_verified': False,
                  'verified_at': datetime.datetime.now().isoformat(timespec='seconds')}
        write_private(STAGING / 'installation-status.json', json.dumps(report, indent=2) + '\n', owner)
        say('INSTALLATION_COMPLETE: six services healthy; HTTPS verified; edge access and archive jobs pending.')
    finally:
        stop_heartbeat.set()
        if LOG:
            LOG.close()


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        # No traceback: stdin to the admin setup subprocess may contain credentials.
        say('INSTALLATION_STOPPED: ' + type(error).__name__ + ': ' + str(error))
        sys.exit(1)
