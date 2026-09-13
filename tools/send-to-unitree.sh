#!/usr/bin/env bash
set -euo pipefail
# Run on the original shuochen computer; password is entered at the SSH prompt.
archive=/home/shuochen/jumpserver-handoff-20260913.tar.gz
[[ -f "$archive" && -f "$archive.sha256" ]] || { echo 'Archive or checksum missing' >&2; exit 1; }
scp "$archive" "$archive.sha256" unitree@172.18.21.114:~/
