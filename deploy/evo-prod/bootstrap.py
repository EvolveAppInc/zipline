"""Initialize a fresh, privately listening Zipline instance without printing credentials."""
import json
import subprocess
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

credentials = json.loads(Path("credentials.private.json").read_text())
origin = "http://127.0.0.1:13000"
with Path("direct-links.sql").open("rb") as sql:
    subprocess.run([
        "docker", "compose", "exec", "-T", "postgresql", "psql",
        "-U", "zipline", "-d", "zipline", "-v", "ON_ERROR_STOP=1",
    ], stdin=sql, stdout=subprocess.DEVNULL, check=True)
try:
    with urlopen(origin + "/api/setup", timeout=30) as response:
        first = json.load(response)["firstSetup"]
except HTTPError as error:
    if error.code == 403 and json.loads(error.read()).get("code") == 9001:
        print("Initial setup already completed.")
        raise SystemExit(0)
    raise
if not first:
    raise RuntimeError("Refusing to initialize an existing database")
request = Request(
    origin + "/api/setup",
    json.dumps({
        "username": credentials["admin_username"],
        "password": credentials["admin_password"],
    }).encode(),
    {"Content-Type": "application/json"},
    method="POST",
)
with urlopen(request, timeout=30) as response:
    result = json.load(response)
    assert result["firstSetup"] is False
    assert result["user"]["username"] == credentials["admin_username"]
    assert result["user"]["role"] == "SUPERADMIN"
    assert result["user"]["view"]["disableTextFiles"] is True
print("Fresh administrator initialized.")
