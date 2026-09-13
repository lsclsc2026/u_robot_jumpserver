#!/usr/bin/env python3
"""Read-only account/login diagnosis; never reads password hashes or credentials."""
import os
from pathlib import Path
import subprocess
import sys

CODE = '''
import json
from users.models import User
from users.utils import LoginBlockUtil, LoginIpBlockUtil
from audits.models import UserLoginLog
from django.utils import timezone
users = list(User.objects.filter(is_service_account=False).order_by('date_joined').values(
    'username', 'is_active', 'source', 'is_first_login', 'need_update_password',
    'date_password_last_updated', 'last_login')[:20])
logs = list(UserLoginLog.objects.order_by('-datetime').values(
    'username', 'datetime', 'status', 'reason_code', 'backend', 'ip')[:12])
for user in users:
    checker = LoginBlockUtil(user['username'], '')
    user['login_blocked'] = checker.is_block()
    user['failed_attempts'] = checker.get_failed_count()
ips = sorted(set(row['ip'] for row in logs if row['ip']))
report = {'checked_at': timezone.now(), 'admin_exists': User.objects.filter(username='admin').exists(),
          'users': users, 'recent_logins': logs,
          'ip_blocks': [{'ip': ip, 'blocked': LoginIpBlockUtil(ip).is_block()} for ip in ips]}
print('AUTH_DIAG_BEGIN')
print(json.dumps(report, default=str, ensure_ascii=True, indent=2))
print('AUTH_DIAG_END')
'''

if __name__ == '__main__':
    if os.geteuid() != 0:
        sys.exit('Run with sudo; this script only reads account status and login records.')
    os.umask(0o077)
    result = subprocess.run(['docker', 'exec', '-i', 'jms_core', 'python',
        '/opt/jumpserver/apps/manage.py', 'shell', '-i', 'python'],
        input='exec(' + repr(CODE) + ')\n', text=True, capture_output=True, timeout=90)
    if result.returncode == 0 and 'AUTH_DIAG_BEGIN\n' in result.stdout and 'AUTH_DIAG_END' in result.stdout:
        report = result.stdout.split('AUTH_DIAG_BEGIN\n', 1)[1].split('AUTH_DIAG_END', 1)[0]
        print(report.strip())
        print('AUTH_DIAG_COMPLETE: read-only; no passwords, accounts or lock counters were changed.')
    else:
        log = Path('/var/lib/jumpserver-laptop-setup/auth-diagnostic-error.log')
        log.write_text(result.stdout + '\n' + result.stderr)
        os.chmod(log, 0o600)
        sys.exit('AUTH_DIAG_FAILED: see protected log ' + str(log))
