# Existing nginx deployment with local files

This deployment runs a fresh Evolve Documents instance under `/SRV/doc`.
The database uses the dedicated `evolve-docs_pgdata` Docker volume. Files use
`/SRV/doc/uploads`; S3 and the existing AWS deployment are independent.
The 4.8.2 image includes the relative session-cookie lifetime correction.

Copy the files in this directory into `/SRV/doc`, plus the repository's
`analytics-gateway/` directory and these files, preserving their paths:

- `deploy/evolve-docs/Dockerfile.zipline`
- `deploy/evolve-docs/patch-session-cookie.mjs`
- `src/server/session-cookie-options.ts`
- `tests/sessionCookies.ts`

Create `.env` from `.env.example` with new database and core secrets, and
`.analytics.env` with `DATABASE_URL` and a separate random `ANALYTICS_SECRET`.
Set mode 0600 on both. Save the initial administrator's `admin_username` and
`admin_password` as JSON in `credentials.private.json`, also mode 0600.
Record the source revision and hashes of deployment files in `SOURCE.json`.
The Compose project is separate from `/SRV/docker-compose.yml`; only its
gateway joins the existing nginx network using `evolve-docs-gateway`.
PostgreSQL is private, and native Zipline and gateway maintenance ports bind
to localhost at 13000 and 18080. Container memory limits total 672 MiB.

Initial setup also sets direct file links as the default for new uploading
accounts, including text files, so recipients get the email form and file
without a separate Zipline viewer.

Build and initialize before exposing the service:

```sh
cd /SRV/doc
mkdir -p uploads public themes backups
chmod 700 backups
docker compose config -q
docker compose build
docker compose up -d postgresql zipline
# Wait until http://127.0.0.1:13000/api/healthcheck succeeds.
python3 bootstrap.py
docker compose up -d analytics-gateway
curl -fsS http://127.0.0.1:18080/analytics/api/health
```

Use `doc.conf.template` with the existing wildcard certificate, Docker DNS
resolver and HTTP redirect for `*.evolveapp.org`. Install it in
`/SRV/nginx/templates`, render/copy it into the existing nginx container's
`/etc/nginx/conf.d/doc.conf`, run `nginx -t`, then reload nginx. Preserve any
previous template/config for rollback. The native Zipline service must not
be the public upstream: file requests pass through the email gate and analytics.
The template streams uploads and byte ranges and retains `Referrer-Policy:
same-origin` for email submissions.

Check the complete flow using temporary uploads that are removed afterwards:

```sh
cd /SRV/doc
GATEWAY_TEST_URL=https://doc.evolveapp.org \
GATEWAY_TEST_ORIGIN=https://doc.evolveapp.org \
GATEWAY_TEST_CREDENTIALS_FILE=/SRV/doc/credentials.private.json \
GATEWAY_DEPLOYMENT_DIR=/SRV/doc \
python3 analytics-gateway/test_live.py
```

If the server's resolver still caches an earlier DNS miss, add
`GATEWAY_TEST_CONNECT_IP=127.0.0.1`. This keeps the public Host and TLS
certificate verification while connecting through the local nginx listener.
Verify public DNS and HTTPS separately from outside the server.

Daily local snapshots include the database, upload files, environment secrets,
configuration and administrator credentials. They have root-only permissions
and seven-day retention. Install `backup.sh` executable and both systemd units
in `/etc/systemd/system`, then enable the timer:

```sh
systemctl daemon-reload
systemctl enable --now evolve-docs-backup.timer
systemctl start evolve-docs-backup.service
```

Restore a snapshot into a separate directory first. Recover configuration and
uploads, start PostgreSQL, and restore its custom-format dump with
`pg_restore -U zipline -d zipline --clean --if-exists`. Rebuild the pinned image
and gateway from this repository before starting the application. Retain the
original core/analytics secrets when restoring existing sessions and grants.
