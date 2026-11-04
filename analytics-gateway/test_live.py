"""Live integration checks with temporary files, against the isolated gateway.

No existing file's policy or contents are changed. Temporary uploads are removed
through Zipline's authenticated API in the finally block.
"""
from http.cookies import SimpleCookie
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen
import uuid

BASE = os.environ.get("GATEWAY_TEST_URL", "http://127.0.0.1:8080")
admin = {}
visitor = {}
rows = []


def request(path, data=None, method="GET", cookies=None, headers=None):
    headers = dict(headers or {})
    if method not in ("GET", "HEAD"):
        headers.setdefault("Origin", "https://doc.evolveplatform.ai")
    if cookies:
        headers["Cookie"] = "; ".join(k + "=" + v for k, v in cookies.items())
    if isinstance(data, dict):
        data = json.dumps(data).encode()
        headers["Content-Type"] = "application/json"
    req = Request(BASE + path, data=data, method=method, headers=headers)
    try:
        response = urlopen(req, timeout=45)
    except HTTPError as e:
        response = e
    with response:
        if cookies is not None:
            for value in response.headers.get_all("Set-Cookie", []):
                parsed = SimpleCookie()
                parsed.load(value)
                for key, item in parsed.items():
                    cookies[key] = item.value
        return response.status, {k.lower(): v for k, v in response.headers.items()}, response.read()


def check(name, condition):
    print(("PASS " if condition else "FAIL ") + name, flush=True)
    rows.append({"check": name, "passed": bool(condition)})
    assert condition, name


class TokenParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.token = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "input" and attrs.get("name") == "token":
            self.token = attrs.get("value")


def gate_token(content):
    parser = TokenParser()
    parser.feed(content.decode())
    return parser.token


def stats(file):
    status, _, body = request("/analytics/api/files/" + file["id"] + "/visits", cookies=admin)
    check("authenticated visit history", status == 200)
    return json.loads(body)


def policy(file, required):
    status, _, body = request("/analytics/api/files/" + file["id"] + "/policy",
        {"require_email": required}, method="PATCH", cookies=admin)
    check("save per-file email requirement", status == 200)


status, _, _ = request("/analytics/api/files")
check("file metadata requires authentication", status == 401)
status, _, _ = request("/analytics/api/health")
check("gateway and upstream healthy", status == 200)
s = json.loads(subprocess.check_output([
    "aws", "ssm", "get-parameter", "--region", "eu-north-1",
    "--name", "/evolve-docs/zipline/secrets", "--with-decryption",
    "--query", "Parameter.Value", "--output", "text",
]))
status, _, body = request("/analytics/api/login",
    {"username": s["admin_username"], "password": s["admin_password"]}, method="POST", cookies=admin)
check("native administrator authentication", status == 200 and "zipline_session" in admin)
status, _, body = request("/analytics/api/files", cookies=admin)
check("administrator file listing", status == 200)
initial = json.loads(body)
existing = {f["id"] for f in initial["files"]}
check("existing files have uploader and upload timestamp",
      all(f["uploaded_by"] and f["uploaded_at"] for f in initial["files"]))

files = []
staff_user = None
try:
    for number in range(5):
        name = f"evolve-analytics-test-{number}.txt"
        content_type = "text/plain"
        contents = ("Evolve integration test " + uuid.uuid4().hex + "\n").encode()
        if number == 1:
            name = "evolve-analytics-url?segment-" + uuid.uuid4().hex + ".txt"
        if number == 2:
            temporary = Path("/opt/zipline/uploads/.evolve-test-" + uuid.uuid4().hex + ".mp4")
            try:
                subprocess.run(["docker", "compose", "exec", "-T", "zipline", "ffmpeg",
                    "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                    "color=c=blue:s=160x90:r=10", "-t", "1", "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-y",
                    "/zipline/uploads/" + temporary.name], check=True)
                contents = temporary.read_bytes()
            finally:
                temporary.unlink(missing_ok=True)
            name = "evolve-analytics-test-video.mp4"
            content_type = "video/mp4"
        if number == 3:
            name = "evolve-analytics-test-audio.wav"
            content_type = "audio/wav"
            import io
            import wave
            output = io.BytesIO()
            with wave.open(output, "wb") as audio:
                audio.setnchannels(1)
                audio.setsampwidth(2)
                audio.setframerate(8000)
                audio.writeframes(b"\0\0" * 8000)
            contents = output.getvalue()
        if number == 4:
            name = "evolve-analytics-test-sandbox.html"
            content_type = "text/html"
            contents = b'<!doctype html><p>Sandbox regression fixture</p><script>document.body.dataset.executed="yes"</script>'
        boundary = "evolveBoundary" + uuid.uuid4().hex
        multipart = (
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{name}"\r\n'
            f'Content-Type: {content_type}\r\n\r\n'
        ).encode() + contents + f"\r\n--{boundary}--\r\n".encode()
        upload_headers = {"Content-Type": "multipart/form-data; boundary=" + boundary,
                          "x-zipline-original-name": "true"}
        if number == 1:
            upload_headers["x-zipline-format"] = "name"
        status, _, body = request("/api/upload", multipart, method="POST", cookies=admin,
            headers=upload_headers)
        check("authenticated upload through gateway", status == 200)
        file = json.loads(body)["files"][0]
        file["path"] = urlsplit(file["url"]).path
        file["data"] = contents
        files.append(file)

    file = files[0]
    policy(file, True)
    status, _, body = request(file["path"], cookies=visitor)
    check("email form precedes protected content", status == 200 and gate_token(body) and file["data"] not in body)
    if BASE.startswith("https://"):
        status, gate_headers, _ = request(file["path"], cookies=visitor)
        check("browser form keeps its same-site origin",
              gate_headers.get("referrer-policy") == "same-origin")
    token = gate_token(body)
    status, _, _ = request(file["path"], method="HEAD")
    check("HEAD cannot bypass email requirement", status == 403)
    status, _, body = request("/raw/" + file["name"])
    check("raw URL cannot bypass email requirement", status == 200 and gate_token(body) and file["data"] not in body)
    status, _, body = request("/view/" + file["name"])
    check("viewer URL cannot bypass email requirement", status == 200 and gate_token(body))
    status, _, body = request(file["path"], headers={"Range": "bytes=4-15"})
    check("byte range cannot bypass email requirement", status == 200 and gate_token(body))
    forged = dict(visitor)
    forged["evolve_visitor"] = forged["evolve_visitor"][:-1] + ("0" if forged["evolve_visitor"][-1] != "0" else "1")
    status, _, body = request(file["path"], cookies=forged)
    check("tampered visitor cookie grants no access", status == 200 and gate_token(body))
    status, _, body = request("/analytics/gate", urlencode({"token": token, "email": "invalid"}).encode(),
        method="POST", cookies=visitor, headers={"Content-Type": "application/x-www-form-urlencoded"})
    check("invalid email rejected", status == 400)
    status, _, _ = request("/analytics/gate", urlencode({"token": token, "email": "client@example.com"}).encode(),
        method="POST", cookies=visitor,
        headers={"Content-Type": "application/x-www-form-urlencoded", "Origin": "https://other.example"})
    check("cross-site email submissions denied", status == 403)
    status, _, body = request("/analytics/gate", urlencode({"token": token, "email": "client@example.com"}).encode(),
        method="POST", cookies=visitor, headers={"Content-Type": "application/x-www-form-urlencoded"})
    # urlopen follows the 303 and urllib does not retain manually passed cookies
    # consistently across redirect implementations. Verify content separately.
    status, _, body = request(file["path"], cookies=visitor)
    check("entered email grants content access", status == 200 and body == file["data"])
    history = stats(file)
    check("email and opening timestamp recorded",
          history["stats"]["visits"] == 1 and history["visits"][0]["email"] == "client@example.com"
          and bool(history["visits"][0]["opened_at"]))
    for byte_range in ("bytes=0-10", "bytes=11-20", "bytes=0-10"):
        status, _, _ = request(file["path"], cookies=visitor, headers={"Range": byte_range})
        check("range streaming preserved", status == 206)
    check("range requests do not inflate visits", stats(file)["stats"]["visits"] == 1)
    status, _, body = request(file["path"], cookies=admin)
    check("authenticated administrator preview allowed", status == 200 and body == file["data"])
    check("administrator previews excluded", stats(file)["stats"]["visits"] == 1)
    status, _, _ = request("/analytics/api/files/" + file["id"] + "/policy",
        {"require_email": False}, method="PATCH")
    check("anonymous policy changes denied", status == 401)
    status, _, _ = request("/analytics/api/files/" + file["id"] + "/policy",
        {"require_email": False}, method="PATCH", cookies=admin, headers={"Origin": "https://other.example"})
    check("cross-site policy changes denied", status == 403)
    policy(file, False)
    anonymous = {}
    status, _, body = request(file["path"], cookies=anonymous)
    check("unprotected files open without a form", status == 200 and body == file["data"])
    history = stats(file)
    check("anonymous view has a timestamp without email",
          history["stats"]["visits"] == 2 and history["visits"][0]["email"] is None
          and bool(history["visits"][0]["opened_at"]))
    policy(file, True)
    status, _, body = request(file["path"], cookies=visitor)
    check("reenabling requirement revokes prior grant", status == 200 and gate_token(body))
    second_visitor = {}
    status, _, body = request(files[1]["path"], cookies=second_visitor)
    check("requirement is independent for every file", status == 200 and body == files[1]["data"])
    for _ in range(2):
        request(files[1]["path"], cookies=second_visitor,
                headers={"Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "document"})
    check("separate browser openings counted individually", stats(files[1])["stats"]["visits"] == 3)
    policy(files[1], True)
    status, _, body = request(files[1]["path"], cookies={})
    check("encoded punctuation in filenames cannot bypass gate",
          status == 200 and gate_token(body) and files[1]["data"] not in body)
    video = files[2]
    policy(video, True)
    video_visitor = {}
    status, _, body = request(video["path"], cookies=video_visitor)
    check("video requires email before playback", status == 200 and gate_token(body))
    token = gate_token(body)
    request("/analytics/gate", urlencode({"token": token, "email": "video@example.com"}).encode(),
        method="POST", cookies=video_visitor, headers={"Content-Type": "application/x-www-form-urlencoded"})
    status, headers, body = request(video["path"], cookies=video_visitor, headers={"Range": "bytes=0-63"})
    check("protected video serves correct range after email",
          status == 206 and body == video["data"][:64] and headers.get("content-type") == "video/mp4")
    check("video ranges retain origin without enabling scripts",
          headers.get("content-security-policy") == "sandbox allow-same-origin")
    request(video["path"], cookies=video_visitor, headers={"Range": "bytes=64-127"})
    history = stats(video)
    check("video playback records one visit with email",
          history["stats"]["visits"] == 1 and history["visits"][0]["email"] == "video@example.com")
    age_visit = """import os,sys,psycopg
with psycopg.connect(os.environ['DATABASE_URL']) as conn:
 conn.execute('UPDATE "EvolveFileVisit" SET opened_at=now()-interval \\'20 minutes\\' WHERE file_id=%s',(sys.argv[1],))
"""
    subprocess.run(["docker", "compose", "exec", "-T", "analytics-gateway", "python", "-c",
                    age_visit, video["id"]], check=True)
    request(video["path"], cookies=video_visitor,
            headers={"Range": "bytes=0-63", "Sec-Fetch-Dest": "video", "Sec-Fetch-Mode": "no-cors"})
    check("seeking after a long playback still counts one opening", stats(video)["stats"]["visits"] == 1)
    audio = files[3]
    policy(audio, True)
    audio_visitor = {}
    status, headers, body = request(audio["path"], cookies=audio_visitor)
    check("audio requires email before playback", status == 200 and gate_token(body))
    request("/analytics/gate", urlencode({"token": gate_token(body), "email": "audio@example.com"}).encode(),
        method="POST", cookies=audio_visitor, headers={"Content-Type": "application/x-www-form-urlencoded"})
    status, headers, body = request(audio["path"], cookies=audio_visitor, headers={"Range": "bytes=0-63"})
    check("protected audio serves byte ranges with cookie-compatible sandbox",
          status == 206 and body == audio["data"][:64]
          and headers.get("content-security-policy") == "sandbox allow-same-origin")
    status, headers, body = request(files[4]["path"], cookies={})
    check("uploaded HTML retains its opaque sandbox",
          status == 200 and body == files[4]["data"]
          and headers.get("content-security-policy") == "sandbox")
    status, _, body = request("/analytics/api/files", cookies=admin)
    updated = json.loads(body)
    new = next(f for f in updated["files"] if f["id"] == file["id"])
    check("new upload shows accountable user and time",
          new["uploaded_by"] == s["admin_username"] and bool(new["uploaded_at"]))
    check("original upload filename preserved", new["original_name"] == "evolve-analytics-test-0.txt")
    check("existing files preserved", existing.issubset({f["id"] for f in updated["files"]}))
    staff_name = "flow-staff-" + uuid.uuid4().hex[:12]
    staff_password = uuid.uuid4().hex + uuid.uuid4().hex
    status, _, body = request("/api/users",
        {"username": staff_name, "password": staff_password, "role": "USER"},
        method="POST", cookies=admin)
    check("administrator can create a staff account", status == 200)
    staff_user = json.loads(body)
    staff = {}
    status, _, _ = request("/analytics/api/login",
        {"username": staff_name, "password": staff_password}, method="POST", cookies=staff)
    check("staff can sign in", status == 200 and "zipline_session" in staff)
    boundary = "evolveBoundary" + uuid.uuid4().hex
    contents = ("Staff upload " + uuid.uuid4().hex + "\n").encode()
    multipart = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="evolve-staff-test.txt"\r\n'
        'Content-Type: text/plain\r\n\r\n').encode() + contents + f"\r\n--{boundary}--\r\n".encode()
    status, _, body = request("/api/upload", multipart, method="POST", cookies=staff,
        headers={"Content-Type": "multipart/form-data; boundary=" + boundary, "x-zipline-original-name": "true"})
    check("staff can upload a file", status == 200)
    staff_file = json.loads(body)["files"][0]
    files.append(staff_file)
    status, _, body = request("/analytics/api/files", cookies=staff)
    own_listing = json.loads(body)
    check("staff see only their own files",
          status == 200 and own_listing["total"] == 1 and own_listing["files"][0]["id"] == staff_file["id"])
    check("upload identifies the actual staff account", own_listing["files"][0]["uploaded_by"] == staff_name)
    status, _, _ = request("/analytics/api/files/" + staff_file["id"] + "/policy",
        {"require_email": True}, method="PATCH", cookies=staff)
    check("staff can configure their own file", status == 200)
    status, _, _ = request("/analytics/api/files/" + file["id"] + "/visits", cookies=staff)
    check("staff cannot read another uploader's analytics", status == 403)
    status, _, _ = request("/analytics/api/files/" + file["id"] + "/policy",
        {"require_email": False}, method="PATCH", cookies=staff)
    check("staff cannot change another uploader's policy", status == 403)
    status, _, body = request("/analytics/api/files", cookies=admin)
    staff_metadata = next(f for f in json.loads(body)["files"] if f["id"] == staff_file["id"])
    check("administrator sees staff uploader and upload time",
          status == 200 and staff_metadata["uploaded_by"] == staff_name and bool(staff_metadata["uploaded_at"]))
finally:
    cleanup_errors = []
    for file in files:
        status, _, _ = request("/api/user/files/" + file["id"], method="DELETE", cookies=admin)
        if status != 200:
            cleanup_errors.append({"id": file["id"], "status": status})
        else:
            print("PASS temporary upload removed", flush=True)
    check("all temporary uploads removed", not cleanup_errors)
    if staff_user:
        status, _, _ = request("/api/users/" + staff_user["id"], {"delete": False},
            method="DELETE", cookies=admin)
        check("temporary staff account removed", status == 200)
    Path("/opt/zipline/analytics-verification.json").write_text(json.dumps(rows, indent=2) + "\n")
print("All analytics integration checks passed.")
