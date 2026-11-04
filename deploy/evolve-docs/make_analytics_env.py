"""Derive the gateway secret from an existing unquoted Zipline .env file."""
import hashlib
import hmac
import os
from pathlib import Path

root = Path(__file__).resolve().parent
target = root / ".analytics.env"
values = {}
for line in (root / ".env").read_text().splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        key, value = line.split("=", 1)
        values[key.strip()] = value

for key in ("CORE_SECRET", "DATABASE_URL"):
    if not values.get(key) or "replace-with" in values[key]:
        raise SystemExit(f"Set {key} in .env before generating the gateway configuration.")
    if values[key].startswith(("'", '"')):
        raise SystemExit(f"{key} must be unquoted in .env for this helper.")

secret = hmac.new(
    values["CORE_SECRET"].encode(),
    b"evolve-analytics-gateway-v1",
    hashlib.sha256,
).hexdigest()
descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(descriptor, "w") as output:
    output.write(f"DATABASE_URL={values['DATABASE_URL']}\nANALYTICS_SECRET={secret}\n")
print("Created .analytics.env with mode 0600. Existing configurations are never overwritten.")
