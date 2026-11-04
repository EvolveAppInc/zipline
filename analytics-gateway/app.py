import base64
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import html
import json
import os
from pathlib import Path
import re
import time
from urllib.parse import quote
import uuid

from email_validator import EmailNotValidError, validate_email
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
import httpx
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

ORIGIN = os.environ.get("PUBLIC_ORIGIN", "https://doc.evolveplatform.ai").rstrip("/")
SECRET = os.environ["ANALYTICS_SECRET"].encode()
if len(SECRET) < 32:
    raise RuntimeError("ANALYTICS_SECRET must be at least 32 characters")
COOKIE = "evolve_visitor"
VISIT_WINDOW = 15 * 60
pool = AsyncConnectionPool(os.environ["DATABASE_URL"], open=False, min_size=1, max_size=5,
                           kwargs={"row_factory": dict_row})
upstream = httpx.AsyncClient(
    base_url=os.environ.get("ZIPLINE_ORIGIN", "http://zipline:3000"),
    timeout=httpx.Timeout(connect=10, read=120, write=120, pool=10),
    follow_redirects=False, trust_env=False,
)


@asynccontextmanager
async def lifespan(_app):
    await pool.open()
    await pool.wait()
    async with pool.connection() as conn:
        await conn.execute(Path("schema.sql").read_text())
        await conn.execute('DELETE FROM "EvolveViewGrant" WHERE expires_at < now()')
        await conn.execute('''
            INSERT INTO "EvolveFilePolicy"(file_id, uploader_name)
            SELECT f.id, u.username FROM "File" f LEFT JOIN "User" u ON u.id=f."userId"
            ON CONFLICT (file_id) DO NOTHING
        ''')
    yield
    await pool.close()
    await upstream.aclose()


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


def signed(payload):
    value = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode().rstrip("=")
    signature = hmac.new(SECRET, value.encode(), hashlib.sha256).hexdigest()
    return value + "." + signature


def verified(value):
    try:
        data, signature = value.rsplit(".", 1)
        expected = hmac.new(SECRET, data.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            return None
        payload = json.loads(base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)))
        if not isinstance(payload, dict) or not isinstance(payload.get("exp"), int):
            return None
        return payload if payload["exp"] > time.time() else None
    except (ValueError, TypeError, UnicodeError):
        return None


def visitor(request):
    payload = verified(request.cookies.get(COOKIE, ""))
    if payload and payload.get("purpose") == "visitor" and re.fullmatch(r"[0-9a-f]{32}", str(payload.get("visitor", ""))):
        return payload["visitor"], False
    return uuid.uuid4().hex, True


def attach_cookie(response, visitor_id, new):
    if new:
        response.set_cookie(COOKIE, signed({"purpose": "visitor", "visitor": visitor_id, "exp": int(time.time()) + 30 * 86400}),
                            max_age=30 * 86400, secure=True, httponly=True, samesite="lax", path="/")
    return response


def check_origin(request):
    origin = request.headers.get("origin")
    if origin and origin != ORIGIN:
        raise HTTPException(403, "Requests from other sites are not allowed")


async def authenticated(request):
    if not request.cookies.get("zipline_session") and not request.headers.get("authorization"):
        raise HTTPException(401, "Sign in to the dashboard")
    headers = {k: request.headers[k] for k in ("cookie", "authorization") if k in request.headers}
    response = await upstream.get("/api/user", headers=headers)
    if response.status_code != 200:
        raise HTTPException(401, "Sign in to the dashboard")
    user = response.json().get("user")
    if not user or not user.get("id"):
        raise HTTPException(401, "Sign in to the dashboard")
    return user


def admin(user):
    return user.get("role") in ("ADMIN", "SUPERADMIN")


async def permitted_file(request, file_id):
    user = await authenticated(request)
    async with pool.connection() as conn:
        row = await (await conn.execute(
            'SELECT f.*, u.username AS uploader FROM "File" f LEFT JOIN "User" u ON u.id=f."userId" WHERE f.id=%s',
            (file_id,),
        )).fetchone()
    if not row:
        raise HTTPException(404, "File not found")
    if not admin(user) and row["userId"] != user["id"]:
        raise HTTPException(403, "You do not have access to this file")
    return row


def json_response(data, status=200):
    return JSONResponse(json.loads(json.dumps(data, default=lambda v: v.isoformat() if hasattr(v, "isoformat") else str(v))), status_code=status,
                        headers={"Cache-Control": "no-store"})


@app.api_route("/", methods=["GET", "HEAD"])
async def home():
    return RedirectResponse("/analytics", status_code=302)


@app.get("/analytics", include_in_schema=False)
@app.get("/analytics/")
async def dashboard():
    return HTMLResponse(Path("dashboard.html").read_text(), headers={
        "Cache-Control": "no-store",
        "Content-Security-Policy": "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'",
        "X-Content-Type-Options": "nosniff",
    })


@app.get("/analytics/api/health")
async def health():
    async with pool.connection() as conn:
        await conn.execute("SELECT 1")
    response = await upstream.get("/api/healthcheck")
    if response.status_code != 200:
        raise HTTPException(503, "Zipline is unavailable")
    return {"pass": True}


class Login(BaseModel):
    username: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=1, max_length=500)


@app.post("/analytics/api/login")
async def login(data: Login, request: Request):
    check_origin(request)
    result = await upstream.post("/api/auth/login", json=data.model_dump(),
                                 headers={"Host": ORIGIN.removeprefix("https://").removeprefix("http://")})
    response = Response(result.content, result.status_code, media_type="application/json",
                        headers={"Cache-Control": "no-store"})
    for value in result.headers.get_list("set-cookie"):
        response.headers.append("set-cookie", value)
    return response


@app.get("/analytics/api/files")
async def list_files(request: Request, page: int = 1, search: str = ""):
    user = await authenticated(request)
    page = max(1, min(page, 100000))
    search = search[:200]
    async with pool.connection() as conn:
        settings = await (await conn.execute('SELECT "filesRoute" FROM "Zipline" LIMIT 1')).fetchone()
        meta = await (await conn.execute('SELECT started_at FROM "EvolveAnalyticsMeta" LIMIT 1')).fetchone()
        await conn.execute('''
            INSERT INTO "EvolveFilePolicy"(file_id, uploader_name)
            SELECT f.id, u.username FROM "File" f LEFT JOIN "User" u ON u.id=f."userId"
            ON CONFLICT (file_id) DO NOTHING
        ''')
        where = '(f."userId"=%s OR %s) AND (f.name ILIKE %s OR f."originalName" ILIKE %s)'
        args = (user["id"], admin(user), "%" + search + "%", "%" + search + "%")
        total = await (await conn.execute('SELECT count(*) AS n FROM "File" f WHERE ' + where, args)).fetchone()
        rows = await (await conn.execute('''
            SELECT f.id, f.name, f."originalName" AS original_name, f.type, f.size,
                   f."createdAt" AS uploaded_at,
                   COALESCE(p.uploader_name,u.username,'Unknown') AS uploaded_by,
                   COALESCE(p.require_email,false) AS require_email,
                   v.visits, v.last_opened, v.identified_visits
            FROM "File" f
            LEFT JOIN "User" u ON u.id=f."userId"
            LEFT JOIN "EvolveFilePolicy" p ON p.file_id=f.id
            LEFT JOIN LATERAL (
                SELECT count(*) AS visits, max(opened_at) AS last_opened,
                       count(email) AS identified_visits
                FROM "EvolveFileVisit" WHERE file_id=f.id
            ) v ON true
            WHERE ''' + where + '''
            ORDER BY f."createdAt" DESC, f.id LIMIT 50 OFFSET %s
        ''', (*args, (page - 1) * 50))).fetchall()
    return json_response({"files": rows, "total": total["n"], "page": page,
                         "file_route": settings["filesRoute"].strip().lower() if settings else "/u",
                         "tracking_since": meta["started_at"], "user": {
        "username": user["username"], "role": user["role"],
    }})


class Policy(BaseModel):
    require_email: bool


@app.patch("/analytics/api/files/{file_id}/policy")
async def update_policy(file_id: str, data: Policy, request: Request):
    check_origin(request)
    file = await permitted_file(request, file_id)
    async with pool.connection() as conn:
        row = await (await conn.execute('''
            INSERT INTO "EvolveFilePolicy"(file_id,require_email,uploader_name)
            VALUES (%s,%s,%s)
            ON CONFLICT (file_id) DO UPDATE SET
                require_email=EXCLUDED.require_email,
                revision="EvolveFilePolicy".revision +
                    CASE WHEN "EvolveFilePolicy".require_email<>EXCLUDED.require_email THEN 1 ELSE 0 END,
                updated_at=now()
            RETURNING require_email,revision
        ''', (file_id, data.require_email, file["uploader"]))).fetchone()
    return row


@app.get("/analytics/api/files/{file_id}/visits")
async def visits(file_id: str, request: Request, page: int = 1):
    file = await permitted_file(request, file_id)
    page = max(1, min(page, 100000))
    async with pool.connection() as conn:
        stats = await (await conn.execute('''
            SELECT count(*) AS visits, count(email) AS identified_visits,
                   count(DISTINCT email) AS unique_emails, max(opened_at) AS last_opened
            FROM "EvolveFileVisit" WHERE file_id=%s
        ''', (file_id,))).fetchone()
        rows = await (await conn.execute('''
            SELECT id, opened_at, email FROM "EvolveFileVisit"
            WHERE file_id=%s ORDER BY opened_at DESC,id LIMIT 50 OFFSET %s
        ''', (file_id, (page - 1) * 50))).fetchall()
    return json_response({"file": {"id": file["id"], "name": file["originalName"] or file["name"]},
                          "stats": stats, "visits": rows, "page": page})


def gate_html(file, visitor_id, target, error=""):
    token = signed({"purpose": "gate", "visitor": visitor_id, "file": file["id"], "target": target,
                    "revision": file["revision"], "exp": int(time.time()) + 900})
    name = html.escape(file["originalName"] or file["name"])
    return f'''<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{name}</title>
<style>body{{margin:0;min-height:100vh;display:grid;place-items:center;background:#f6f7f9;color:#172033;font:16px system-ui}}
main{{max-width:390px;width:calc(100% - 80px);padding:32px;background:white;border:1px solid #e4e7ec;border-radius:16px}}
h1{{font-size:20px;overflow-wrap:anywhere}}p{{line-height:1.5;color:#526075}}label{{display:block;margin-bottom:8px}}
input,button{{width:100%;box-sizing:border-box;padding:12px;border:1px solid #cdd4df;border-radius:8px;font:inherit}}
button{{margin-top:16px;background:#214ed3;color:white;border:0;cursor:pointer}}small{{display:block;margin-top:18px;color:#69758a;line-height:1.4}}
.error{{color:#ad2433}}</style><main><h1>{name}</h1><p>Enter your email address to open this file.</p>
<form method="post" action="/analytics/gate"><input type="hidden" name="token" value="{html.escape(token, quote=True)}">
<label for="email">Email</label><input id="email" name="email" type="email" maxlength="254" required autocomplete="email" autofocus>
<p class="error">{html.escape(error)}</p><button>Open file</button></form>
<small>The file owner will see the email address you enter and when you open the file. Email verification is not required.</small></main></html>'''


@app.post("/analytics/gate")
async def gate_submit(request: Request):
    check_origin(request)
    if int(request.headers.get("content-length", "0")) > 4096:
        raise HTTPException(413)
    body = bytearray()
    async for part in request.stream():
        body.extend(part)
        if len(body) > 4096:
            raise HTTPException(413)
    from urllib.parse import parse_qs
    form = parse_qs(body.decode("utf-8"))
    token = verified(form.get("token", [""])[0])
    visitor_id, new = visitor(request)
    if not token or token.get("purpose") != "gate" or new or token.get("visitor") != visitor_id:
        raise HTTPException(403, "This form has expired. Open the link again.")
    target = token.get("target", "")
    if not target.startswith("/") or target.startswith("//") or "\r" in target or "\n" in target:
        raise HTTPException(400)
    async with pool.connection() as conn:
        file = await (await conn.execute('''
            SELECT f.*, p.require_email, p.revision
            FROM "File" f JOIN "EvolveFilePolicy" p ON p.file_id=f.id WHERE f.id=%s
        ''', (token.get("file"),))).fetchone()
        if not file or token.get("revision") != file["revision"]:
            raise HTTPException(403, "The file settings have changed. Open the link again.")
        try:
            email = validate_email(form.get("email", [""])[0].strip(),
                                   check_deliverability=False).normalized
        except EmailNotValidError:
            return HTMLResponse(gate_html(file, visitor_id, target, "Enter a valid email address."),
                                status_code=400, headers={"Cache-Control": "no-store"})
        await conn.execute('''
            INSERT INTO "EvolveViewGrant"(visitor_id,file_id,email,revision,expires_at)
            VALUES (%s,%s,%s,%s,now()+interval '7 days')
            ON CONFLICT (visitor_id,file_id) DO UPDATE SET
                email=EXCLUDED.email, revision=EXCLUDED.revision, expires_at=EXCLUDED.expires_at
        ''', (visitor_id, file["id"], email, file["revision"]))
    return RedirectResponse(target, status_code=303, headers={"Cache-Control": "no-store"})


async def find_public_file(request):
    # ASGI's decoded path preserves literal '?' and '#' in filenames. Parsing
    # it as a URL would incorrectly treat those characters as query/fragment.
    path = request.scope["path"]
    async with pool.connection() as conn:
        settings = await (await conn.execute(
            'SELECT "filesRoute", "filesExtensionlessUrls" FROM "Zipline" LIMIT 1'
        )).fetchone()
        route = settings["filesRoute"].strip().lower() if settings else "/u"
        prefixes = (route + "/", "/raw/", "/view/")
        prefix = next((p for p in prefixes if path.startswith(p)), None)
        if not prefix:
            return None, None
        name = path[len(prefix):]
        if prefix == "/view/" and name.startswith("url/"):
            return None, None
        if not name or name in (".", "..") or "/" in name or "\\" in name or "\x00" in name:
            return None, None
        if name.startswith(".thumbnail"):
            row = await (await conn.execute('''
                SELECT f.*, COALESCE(p.require_email,false) AS require_email,
                       COALESCE(p.revision,1) AS revision
                FROM "Thumbnail" t JOIN "File" f ON f.id=t."fileId"
                LEFT JOIN "EvolveFilePolicy" p ON p.file_id=f.id WHERE t.path=%s
            ''', (name,))).fetchone()
            return row, "thumbnail"
        row = await (await conn.execute('''
            SELECT f.*, COALESCE(p.require_email,false) AS require_email,
                   COALESCE(p.revision,1) AS revision
            FROM "File" f LEFT JOIN "EvolveFilePolicy" p ON p.file_id=f.id
            WHERE f.name=%s
        ''', (name,))).fetchone()
        if not row and settings and settings["filesExtensionlessUrls"] and "." not in name:
            escaped = name.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            row = await (await conn.execute('''
                SELECT f.*, COALESCE(p.require_email,false) AS require_email,
                       COALESCE(p.revision,1) AS revision
                FROM "File" f LEFT JOIN "EvolveFilePolicy" p ON p.file_id=f.id
                WHERE f.name LIKE %s ORDER BY f."createdAt" DESC LIMIT 1
            ''', (escaped + ".%",))).fetchone()
        return row, "page" if prefix == "/view/" else "content"


async def record_visit(file, visitor_id, email, navigation=False, media_range=False):
    async with pool.connection() as conn:
        # Serialize first-load and concurrent video range requests for this viewer/file.
        await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                           (visitor_id + ":" + file["id"],))
        last = await (await conn.execute('''
            SELECT opened_at,email FROM "EvolveFileVisit" WHERE file_id=%s AND visitor_id=%s
            ORDER BY opened_at DESC LIMIT 1
        ''', (file["id"], visitor_id))).fetchone()
        now = datetime.now(timezone.utc)
        if not navigation and media_range and last and last["email"] == email:
            return
        if not navigation and last and last["email"] == email and now - last["opened_at"] < timedelta(seconds=VISIT_WINDOW):
            return
        await conn.execute('''
            INSERT INTO "EvolveFileVisit"(id,file_id,visitor_id,email) VALUES (%s,%s,%s,%s)
        ''', (uuid.uuid4(), file["id"], visitor_id, email))


HOP_HEADERS = {"host", "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
               "te", "trailer", "transfer-encoding", "upgrade"}


async def proxy(request, file=None, kind=None, visitor_id=None, new=False, email=None, internal=False):
    raw_path = request.scope.get("raw_path", b"/")
    if request.scope.get("query_string"):
        raw_path += b"?" + request.scope["query_string"]
    # Keep percent-encoded filenames intact and always retain the fixed upstream
    # authority, even for request paths starting with two slashes.
    target = upstream.base_url.copy_with(raw_path=raw_path)
    headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP_HEADERS}
    headers["host"] = request.headers.get("host", ORIGIN.removeprefix("https://"))
    # Preserve bytes and native content headers; do not decompress a proxied response.
    headers["accept-encoding"] = "identity"
    has_body = (request.headers.get("content-length", "0") != "0"
                or bool(request.headers.get("transfer-encoding")))
    outgoing = upstream.build_request(request.method, target, headers=headers,
                                      content=request.stream() if has_body else None)
    try:
        result = await upstream.send(outgoing, stream=True)
    except httpx.HTTPError:
        raise HTTPException(503, "The file is temporarily unavailable")
    if file and kind == "content" and request.method == "GET" and result.status_code in (200, 206) and not internal:
        range_header = request.headers.get("range")
        # Later byte ranges (seeking) must not become new visits.
        navigation = (request.headers.get("sec-fetch-mode") == "navigate"
                      and request.headers.get("sec-fetch-dest") == "document")
        if navigation or not range_header or re.match(r"^bytes=0(?:-|,)", range_header):
            try:
                media_range = (bool(range_header) and request.headers.get("sec-fetch-dest") in
                               ("video", "audio", "image", "embed"))
                await record_visit(file, visitor_id, email, navigation, media_range)
            except Exception:
                await result.aclose()
                raise
    response = StreamingResponse(result.aiter_raw(), status_code=result.status_code,
                                 background=BackgroundTask(result.aclose))
    response.raw_headers = [(k, v) for k, v in result.headers.raw
                            if k.decode().lower() not in HOP_HEADERS | {"date", "server"}]
    if file:
        content_type = result.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if kind == "content" and content_type.startswith(("video/", "audio/")):
            # Native media documents fetch their own URL again for byte ranges.
            # An opaque sandbox origin drops SameSite cookies on those requests,
            # so the player receives the email gate instead of media bytes.
            # Keep scripts sandboxed; only restore the origin for passive media.
            if response.headers.get("content-security-policy", "").strip() == "sandbox":
                response.headers["content-security-policy"] = "sandbox allow-same-origin"
        response.headers["cache-control"] = "private, no-store"
        response.headers["x-robots-tag"] = "noindex, nofollow"
        attach_cookie(response, visitor_id, new)
    return response


@app.api_route("/{path:path}", methods=["GET", "HEAD", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"])
async def gateway(path: str, request: Request):
    if request.method not in ("GET", "HEAD"):
        return await proxy(request)
    file, kind = await find_public_file(request)
    if not file:
        return await proxy(request)
    visitor_id, new = visitor(request)
    internal = False
    if request.cookies.get("zipline_session") or request.headers.get("authorization"):
        try:
            user = await authenticated(request)
            internal = admin(user) or file["userId"] == user["id"]
        except HTTPException:
            pass
    email = None
    if file["require_email"] and not internal:
        async with pool.connection() as conn:
            grant = await (await conn.execute('''
                SELECT email FROM "EvolveViewGrant" WHERE visitor_id=%s AND file_id=%s
                AND revision=%s AND expires_at>now()
            ''', (visitor_id, file["id"], file["revision"]))).fetchone()
        if not grant:
            if request.method == "HEAD" or kind == "thumbnail":
                return Response(status_code=403, headers={"Cache-Control": "no-store"})
            query = request.scope.get("query_string", b"").decode("utf-8")
            target = request.scope.get("raw_path", b"/").decode("utf-8") + (
                "?" + query if query else "")
            response = HTMLResponse(gate_html(file, visitor_id, target),
                                    headers={"Cache-Control": "no-store", "X-Robots-Tag": "noindex, nofollow",
                                             "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'"})
            return attach_cookie(response, visitor_id, new)
        email = grant["email"]
    return await proxy(request, file, kind, visitor_id, new, email, internal)
