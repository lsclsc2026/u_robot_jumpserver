#!/usr/bin/env bash
# Local interactive administrator recovery. No passwords in arguments or reports.
set -euo pipefail
if [[ $(id -u) != 0 ]]; then
  echo 'Run: sudo bash /home/ksxq/recover-jumpserver-admin.sh'
  exit 1
fi
if [[ ! -t 0 || ! -t 1 ]]; then
  echo 'Run directly in the laptop terminal without tee or output redirection.'
  exit 1
fi
umask 077
exec 9>/run/lock/jumpserver-admin-recovery.lock
flock -n 9 || { echo 'Another recovery is running.'; exit 1; }

echo '1/3 Verify admin account (no password read)'
docker exec -i jms_core python /opt/jumpserver/apps/manage.py shell -i python <<'PY'
from users.models import User
u = User.objects.get(username='admin')
assert u.is_active and u.is_local, 'admin must be an active local account'
print('ADMIN_ACCOUNT_VERIFIED')
PY

echo '2/3 Set the password using the built-in interactive command'
echo 'Type a strong new password twice. Input is hidden. Do not share it in chat.'
docker exec -it jms_core python /opt/jumpserver/apps/manage.py changepassword admin

echo '3/3 Clear only admin login failures and verify account state'
docker exec -i jms_core python /opt/jumpserver/apps/manage.py shell -i python <<'PY'
exec("import json\nfrom users.models import User\nfrom users.utils import LoginBlockUtil\nu = User.objects.get(username='admin')\nassert u.is_active and u.is_local and u.has_usable_password(), 'Account verification failed'\nu.need_update_password = False\nu.save(update_fields=['need_update_password'])\nlimiter = LoginBlockUtil('admin', '')\nlimiter.clean_failed_count()\nu.refresh_from_db()\nassert not limiter.is_block() and limiter.get_failed_count() == 0\nassert not u.need_update_password\nprint(json.dumps({'username': u.username, 'is_active': u.is_active, 'need_update_password': u.need_update_password, 'password_updated_at': str(u.date_password_last_updated), 'failed_attempts': limiter.get_failed_count(), 'login_blocked': limiter.is_block()}))\nprint('ADMIN_RECOVERY_COMPLETE: password updated; admin login failures cleared; browser login still needs verification.')")
PY
