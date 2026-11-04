# Deploy Evolve Documents

The tested stack is Zipline 4.8.2, PostgreSQL 16, Caddy 2.10.2, and the Evolve
analytics gateway. The gateway is built from this fork; the native Zipline image
is pinned to the upstream release used in the audited deployment.

The sample Compose stack is for a new installation. For the existing EC2
deployment, use the update instructions below to keep its database, volumes,
environment files, and S3 configuration.

## New installation

Run from `deploy/evolve-docs` on a host with Docker Compose:

```sh
cp .env.example .env
chmod 600 .env
```

Edit `.env`: set the domain/origin, a strong URL-safe database password, a long
random `CORE_SECRET`, and the private S3 bucket credentials. Use the same database
password in `POSTGRESQL_PASSWORD` and `DATABASE_URL`. The helper expects unquoted
values. Generate `.analytics.env` from that existing configuration:

```sh
python3 make_analytics_env.py
docker compose up -d --build
```

Point the domain's DNS A record at the host and allow public ports 80/443.
For Cloudflare, DNS-only mode avoids its proxy upload-size limit. Caddy handles
HTTPS certificates. Complete the initial administrator setup through `/dashboard`,
then use `/analytics` for file sharing and opening history. Disable public
registration and invitations in the administrator settings.

Keep S3 public access blocked. Use private application credentials restricted
to the file prefix, and use separate backup credentials or an EC2 instance role
for database backups.

## Update the existing EC2 gateway

The existing installation is under `/opt/zipline`. Its native `.env`,
`.analytics.env`, `compose.yaml`, `compose.override.yaml`, `Caddyfile`,
Docker volumes, and backups already hold the production configuration.

After cloning this fork on the server, copy the gateway source into the existing
deployment and rebuild only that service. In the example below, the clone is
`/opt/zipline-source`:

```sh
cp /opt/zipline-source/analytics-gateway/{app.py,dashboard.html,schema.sql,Dockerfile,requirements.txt,requirements.lock,test_live.py} \
  /opt/zipline/analytics-gateway/
cd /opt/zipline
DOCKER_BUILDKIT=0 docker build -t evolve-docs-analytics:1.0.0 analytics-gateway
docker compose up -d --no-deps analytics-gateway
curl -fsS http://127.0.0.1:8080/analytics/api/health
```

The deployed source was copied into this fork without changes to the gateway
application, dashboard, or database schema. The fork's Dockerfile installs the
recorded dependency lock instead of resolving version ranges again.

## Backup and recovery

Preserve the database, native and gateway secrets, source/configuration, and
private S3 objects. S3 file objects alone cannot restore analytics or accounts.
The gateway's `Evolve*` tables are included in ordinary PostgreSQL backups:

```sh
docker compose exec -T postgresql pg_dump -U zipline -d zipline > backup.sql
```

The current EC2 installation already has a daily database backup timer. A source
snapshot was also saved after the 2026-11-04 audit. This fork now provides a
versioned copy of the source.

When restoring an existing database, retain the original `CORE_SECRET` and
`ANALYTICS_SECRET` so signed visitor cookies keep their meaning. If the gateway
secret file is missing, the helper's HMAC label matches the deployed derivation.
Keep `Referrer-Policy: same-origin` and route all public traffic through the
gateway. Bind Zipline and the gateway to localhost; expose only Caddy publicly.

Production credentials, account/resource IDs, client files, database dumps,
cookies, and audit output are not part of this repository.
