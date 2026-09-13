#!/usr/bin/env python3
"""Add the missing pinned execution image; check network and read asset grants."""
import ast
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import datetime
import tempfile
import secrets

STAGE = Path('/home/ksxq/jumpserver-deployment')
REF = 'jumpserver/ansible-executor:latest'
CONFIG_DIGEST = 'sha256:f0b630feace885d41d407dc2548358c460afdc62919d8d159a5d1a0ff9fa557c'
MANIFEST_DIGEST = 'sha256:42dd7564a9a50d239ae1502b426b132f3431a357827f55f5fa8fe549a7430750'
ARCHIVE_SHA256 = '748b22003de349671375c9a7253ccb30008c382a6212bc6f61bde225c3b333e2'

ASSET_REPORT_CODE = '''
import json
from assets.models import Asset
from accounts.models import Account
from users.models import User
from perms.utils.permission import AssetPermissionUtil
from perms.utils.user_perm import UserPermAssetUtil
from orgs.utils import tmp_to_root_org
with tmp_to_root_org():
    u = User.objects.get(username='admin')
    assets = Asset.objects.filter(address='100.119.208.88')
    data = []
    effective = set(UserPermAssetUtil(u).get_all_assets().filter(address='100.119.208.88').values_list('id', flat=True))
    for a in assets:
        data.append({'name': a.name, 'address': a.address, 'active': a.is_active,
                     'platform': a.platform.name, 'nodes': list(a.nodes.values_list('value', flat=True)),
                     'admin_has_asset_permission': a.id in effective,
                     'accounts': list(Account.objects.filter(asset=a).values('name', 'username', 'secret_type', 'privileged'))})
    rules = []
    for p in AssetPermissionUtil().get_permissions_for_user(u, with_expired=True)[:20]:
        rules.append({'name': p.name, 'valid_now': p.is_valid, 'accounts': p.accounts,
                      'assets': list(p.assets.values_list('name', flat=True)),
                      'nodes': list(p.nodes.values_list('value', flat=True)),
                      'protocols': p.protocols, 'actions': p.actions})
    print('ASSET_REPORT_BEGIN')
    print(json.dumps({'assets': data, 'admin_rules': rules}, ensure_ascii=False, indent=2))
    print('ASSET_REPORT_END')
'''


def ensure_data_alias(data, alias):
    if not data.is_dir():
        raise RuntimeError('Core data directory is missing')
    if os.path.lexists(alias):
        if not alias.is_symlink() or alias.resolve() != data.resolve():
            raise RuntimeError('Existing host core data path needs review; nothing overwritten')
    else:
        alias.symlink_to(data, target_is_directory=True)


def main():
    if os.geteuid() != 0:
        sys.exit('Run: sudo python3 /home/ksxq/add-jumpserver-executor.py')
    os.umask(0o077)
    spec = importlib.util.spec_from_file_location('installer', '/home/ksxq/install-jumpserver-laptop.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    item = json.loads((STAGE / 'ansible-image.json').read_text())
    module.require(item['ref'] == REF and item['image_id'] == CONFIG_DIGEST
                   and item['manifest_digest'] == MANIFEST_DIGEST
                   and item['file'] == 'jumpserver_ansible-executor_pinned.tar', 'Unexpected image metadata')
    module.require(module.digest(STAGE / 'images' / item['file']) == ARCHIVE_SHA256 == item['sha256'],
                   'Image archive checksum mismatch')
    log_path = Path('/var/lib/jumpserver-laptop-setup') / ('add-executor-' + datetime.datetime.now().strftime('%Y%m%d-%H%M%S') + '.log')
    print('Protected detailed log:', log_path, flush=True)
    with log_path.open('x') as log:
        module.LOG = log
        module.EXPECTED_IMAGES[REF] = CONFIG_DIGEST
        print('1/4 Load and verify the pinned official Ansible execution image', flush=True)
        module.load_offline_image(item)
        module.run(['docker', 'exec', 'jms_celery', 'docker', 'image', 'inspect', REF], capture=True)
        print('CELERY_CAN_SEE_EXECUTOR_IMAGE', flush=True)
        print('2/4 Verify task-directory mount, executor runtime and network', flush=True)
        mounts = json.loads(module.run(['docker', 'inspect', '--format', '{{json .Mounts}}', 'jms_core'], capture=True))
        data_mounts = [x for x in mounts if x['Destination'] == '/opt/jumpserver/data']
        module.require(len(data_mounts) == 1 and data_mounts[0]['Source'] == '/data/jumpserver/core/data',
                       'Unexpected core data mount; review required')
        data = Path(data_mounts[0]['Source'])
        alias = Path('/opt/jumpserver/data')
        ensure_data_alias(data, alias)
        with tempfile.TemporaryDirectory(prefix='.executor-check-', dir=data) as temporary:
            token = secrets.token_hex(16)
            (Path(temporary) / 'sentinel').write_text(token)
            bind_source = alias / Path(temporary).name
            observed = module.run(['docker', 'run', '--rm', '--pull', 'never', '--network', 'none',
                                   '--user', '0:0',
                                   '--mount', 'type=bind,src=' + str(bind_source) + ',dst=/check,readonly',
                                   '--entrypoint', '/usr/bin/python3.14', REF, '-c',
                                   "from pathlib import Path; print(Path('/check/sentinel').read_text())"],
                                  capture=True, timeout=90)
            module.require(observed == token, 'Executor cannot read the mapped task directory')
        print('EXECUTOR_TASK_MOUNT_VERIFIED', flush=True)
        probe = "import ansible,socket; s=socket.create_connection(('100.119.208.88',22),10); s.settimeout(10); print(s.recv(256).decode().strip()); s.close()"
        banner = module.run(['docker', 'run', '--rm', '--pull', 'never', '--network', 'jms_net',
                             '--entrypoint', '/usr/bin/python3.14', REF, '-c', probe], capture=True, timeout=90)
        module.require('SSH-2.0-' in banner, 'Executor did not receive an SSH banner')
        print('EXECUTOR_TO_ASSET_SSH_PORT_OK', flush=True)
        print('3/4 Verify the Koko network namespace can reach the asset', flush=True)
        banner = module.run(['docker', 'run', '--rm', '--pull', 'never', '--network', 'container:jms_koko',
                             '--entrypoint', '/usr/bin/python3.14', REF, '-c', probe], capture=True, timeout=90)
        module.require('SSH-2.0-' in banner, 'Koko network did not receive an SSH banner')
        print('KOKO_NETWORK_TO_ASSET_SSH_PORT_OK', flush=True)
        print('4/4 Read asset nodes and effective admin grants (no passwords)', flush=True)
        output = module.run(['docker', 'exec', '-i', 'jms_core', 'python', '/opt/jumpserver/apps/manage.py',
                             'shell', '-i', 'python'], input_text='exec(' + repr(ASSET_REPORT_CODE) + ')\n', capture=True)
        module.require('ASSET_REPORT_BEGIN\n' in output and 'ASSET_REPORT_END' in output, 'Asset report missing')
        report = output.split('ASSET_REPORT_BEGIN\n', 1)[1].split('ASSET_REPORT_END', 1)[0]
        print(report.strip(), flush=True)
        report_path = STAGE / 'asset-access-diagnostic.json'
        module.write_private(report_path, report.strip() + '\n', __import__('pwd').getpwnam('ksxq'))
    print('EXECUTOR_PREPARATION_COMPLETE: image ready and network checked; no accounts or grants were changed.', flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('EXECUTOR_PREPARATION_STOPPED:', type(error).__name__, str(error), flush=True)
        sys.exit(1)
