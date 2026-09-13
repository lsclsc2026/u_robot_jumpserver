#!/usr/bin/env python3
"""Read-only account/login diagnosis; checks the supplied initial password without displaying or modifying stored hashes."""
import os
import getpass
import json
from pathlib import Path
import subprocess
import sys

CODE = "\nimport json\nfrom django.contrib.auth.hashers import check_password\nfrom users.models import User\nfrom users.utils import LoginBlockUtil, LoginIpBlockUtil\nfrom audits.models import UserLoginLog\nfrom django.utils import timezone\npayload = json.loads(__JMS_INPUT_JSON__)\nnames = payload['names']\nrows=[]\nfor u in User.objects.filter(username__in=names).order_by('username'):\n    checker=LoginBlockUtil(u.username, '')\n    rows.append({'username':u.username,\n        'initial_password_matches':check_password(payload['initial_password'],u.password),\n        'is_active':u.is_active,'source':u.source,\n        'need_update_password':u.need_update_password,\n        'date_expired':u.date_expired,\n        'date_password_last_updated':u.date_password_last_updated,\n        'last_login':u.last_login,\n        'login_blocked':checker.is_block(),\n        'failed_attempts':checker.get_failed_count()})\nlogs=list(UserLoginLog.objects.filter(username__in=names).order_by('-datetime').values(\n    'username','datetime','status','reason_code','backend','ip')[:20])\nips=sorted(set(x['ip'] for x in logs if x['ip']))\nprint('AUTH_DIAG_BEGIN')\nprint(json.dumps({'checked_at':timezone.now(),'users':rows,'recent_logins':logs,\n    'ip_blocks':[{'ip':ip,'blocked':LoginIpBlockUtil(ip).is_block()} for ip in ips]},default=str,indent=2))\nprint('AUTH_DIAG_END')\n"

if __name__ == '__main__':
    if os.geteuid() != 0:
        sys.exit('Run with sudo; this script only reads account status and login records.')
    os.umask(0o077)
    if not sys.stdin.isatty():
        sys.exit('Run interactively; credentials must be entered without echo.')
    names = input('Usernames to inspect (space-separated): ').split()
    if not names:
        sys.exit('No usernames supplied; nothing queried.')
    initial_password = getpass.getpass('Initial password to check: ')
    if not initial_password:
        sys.exit('Empty password is not accepted.')
    runtime_code = CODE.replace('__JMS_INPUT_JSON__', repr(json.dumps(
        {'names': names, 'initial_password': initial_password})), 1)
    result = subprocess.run(['docker', 'exec', '-i', 'jms_core', 'python',
        '/opt/jumpserver/apps/manage.py', 'shell', '-i', 'python'],
        input='exec(' + repr(runtime_code) + ')\n', text=True, capture_output=True, timeout=90)
    if result.returncode == 0 and 'AUTH_DIAG_BEGIN\n' in result.stdout and 'AUTH_DIAG_END' in result.stdout:
        report = result.stdout.split('AUTH_DIAG_BEGIN\n', 1)[1].split('AUTH_DIAG_END', 1)[0]
        print(report.strip())
        print('AUTH_DIAG_COMPLETE: read-only; no passwords, accounts or lock counters were changed.')
    else:
        log = Path('/var/lib/jumpserver-laptop-setup/operator-login-diagnostic-error.log')
        log.write_text(result.stdout + '\n' + result.stderr)
        os.chmod(log, 0o600)
        sys.exit('AUTH_DIAG_FAILED: see protected log ' + str(log))
