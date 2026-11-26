#!/bin/bash
set -euo pipefail
umask 077
cd /SRV/doc
exec 9>/SRV/doc/.backup.lock
flock -n 9 || exit 0
mkdir -p backups
chmod 700 backups
work=$(mktemp -d /SRV/doc/backups/.snapshot-XXXXXX)
trap 'rm -rf "$work"' EXIT
docker compose exec -T postgresql pg_dump -U zipline -d zipline -Fc > "$work/database.dump"
cp .env .analytics.env compose.yaml SOURCE.json credentials.private.json "$work/"
if [ -f /SRV/nginx/templates/doc.conf.template ]; then
    cp /SRV/nginx/templates/doc.conf.template "$work/"
fi
archive="/SRV/doc/backups/doc-$(date -u +%Y%m%dT%H%M%SZ).tgz"
# New writes during the archive may be included in the following daily snapshot.
# GNU tar's status 1 means a file changed during reading; larger statuses are failures.
status=0
tar --exclude=uploads/.tmp --exclude=uploads/.chunks \
    -czf "$archive.partial" -C "$work" . -C /SRV/doc \
    uploads public themes analytics-gateway deploy src tests bootstrap.py backup.sh || status=$?
if [ "$status" -gt 1 ]; then
    rm -f "$archive.partial"
    exit "$status"
fi
mv "$archive.partial" "$archive"
find backups -maxdepth 1 -type f -name 'doc-*.tgz' -mtime +7 -delete
printf 'Backup saved: %s\n' "$archive"
