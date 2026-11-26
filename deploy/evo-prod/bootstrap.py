"""Initialize a fresh, privately listening Zipline instance without printing credentials."""
import json
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

credentials = json.loads(Path("credentials.private.json").read_text())
origin = "http://127.0.0.1:13000"
try:
    with urlopen(origin + "/api/setup", timeout=30) as response:
        first = json.load(response)["firstSetup"]
except HTTPError as error:
    if error.code == 400 and json.loads(error.read()).get("code") == 9001:
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
print("Fresh administrator initialized.")
