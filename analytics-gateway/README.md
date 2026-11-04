# Evolve Documents gateway

The gateway runs in front of Zipline 4.8.2 and uses its PostgreSQL database,
staff accounts, uploads, and S3 storage. The native Zipline application remains
available at `/dashboard`; `/` redirects to the English admin panel at `/analytics`.

- Upload one or more files and copy a direct client link.
- Enable email entry independently for each file. Email verification is not required.
- Record opening times with the entered email, or without email for public files.
- Show the uploading account and upload time.
- Owners manage their own files; administrators manage all files.
- Owner/admin previews bypass the client gate and are excluded from analytics.
- A browser's email grant lasts seven days. A policy change invalidates older grants.

Analytics count openings, not reading completion or completed playback. New
navigations count separately; video seeking and range requests are deduplicated.
The display timezone is Europe/Istanbul. History begins when the gateway schema
is first initialized; older Zipline aggregate counters are not backfilled.

`app.py`, `dashboard.html`, and `schema.sql` match the audited production source.
The Docker build uses the dependency versions recorded in `requirements.lock`.

## Request handling

All public routes, including the configured file route, `/raw`, `/view`, and
thumbnails, must pass through the gateway. Zipline's port must remain bound to
localhost or the private Docker network.

The client form uses signed visitor cookies and signed form tokens.
Cross-site form submissions and policy changes are rejected. Caddy must keep
`Referrer-Policy: same-origin`: `no-referrer` caused legitimate browser form
submissions to send `Origin: null`.

For native video/audio responses, `sandbox allow-same-origin` preserves the
SameSite cookie on the player's subsequent byte-range requests. Scripts remain
disabled. Uploaded HTML retains Zipline's original opaque `sandbox`.

## Validation

The public HTTPS deployment passed **70 live integration assertions on
2026-11-04**, including protected alternate routes, byte ranges, cookie tampering,
origin checks, policy revocation, media sandbox behavior, uploader identity,
staff isolation, admin previews, and cleanup.

The browser audit checked login, individual/batch upload, copying links, email
entry, JSON/TXT display, PDF display in Chrome, MP4 playback/seek, ZIP download
with exact byte comparison, opening history, search, and email policy changes.

`test_live.py` is an explicit live integration test, not a unit test. It creates
temporary uploads and a temporary staff account and removes them in `finally`.
It reads admin credentials from the existing AWS SSM SecureString
`/evolve-docs/zipline/secrets` in `eu-north-1`; credentials are never committed.
Run it from the EC2 deployment directory, with the gateway source installed:

```sh
cd /opt/zipline
GATEWAY_TEST_URL=https://doc.evolveplatform.ai python3 analytics-gateway/test_live.py
```

It requires AWS CLI, Python 3, Docker Compose, and the Zipline container's ffmpeg.
It writes the assertion results to `/opt/zipline/analytics-verification.json`.
Small synthetic files were tested; near-limit 2 GB uploads, load, mobile devices,
Safari/Firefox, and every media codec were not tested.
