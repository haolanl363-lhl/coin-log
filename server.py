#!/usr/bin/env python3
"""Small, dependency-free, multi-user crypto journal for local use or self-hosting."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse
import hashlib, hmac, json, os, secrets, time

ROOT = Path(__file__).resolve().parent
PUBLIC = ROOT / "public"
DATA = Path(os.environ.get("COINLOG_DATA_DIR", ROOT / "data"))
DATA.mkdir(exist_ok=True)
SESSIONS = {}
COOKIE = "coinlog_session"
DATABASE_URL = os.environ.get("DATABASE_URL", "")

def db_connection():
    import psycopg
    return psycopg.connect(DATABASE_URL, connect_timeout=15)

def initialize_database():
    if DATABASE_URL:
        with db_connection() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS coinlog_documents (key TEXT PRIMARY KEY, value JSONB NOT NULL)")

def write_json(path, value):
    if DATABASE_URL:
        from psycopg.types.json import Jsonb
        with db_connection() as conn:
            conn.execute("INSERT INTO coinlog_documents (key, value) VALUES (%s, %s) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value", (path.name, Jsonb(value)))
    else:
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

def read_json(path, default):
    if DATABASE_URL:
        with db_connection() as conn:
            row = conn.execute("SELECT value FROM coinlog_documents WHERE key = %s", (path.name,)).fetchone()
            return row[0] if row else default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default

def user_file(uid):
    return DATA / (uid + ".json")

class Handler(BaseHTTPRequestHandler):
    server_version = "CoinLog/0.1"

    def log_message(self, fmt, *args):
        print("%s - %s" % (self.address_string(), fmt % args))

    def send_json(self, obj, code=200, headers=None):
        raw = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        if headers:
            for k, v in headers.items(): self.send_header(k, v)
        self.end_headers(); self.wfile.write(raw)

    def body(self):
        n = int(self.headers.get("Content-Length", "0"))
        if n > 8_000_000: raise ValueError("请求内容过大")
        return json.loads(self.rfile.read(n) or b"{}")

    def current_user(self):
        raw = self.headers.get("Cookie", "")
        token = next((x.split("=",1)[1] for x in raw.split(";") if x.strip().startswith(COOKIE+"=")), "")
        session = SESSIONS.get(token)
        if not session or session["expires"] < time.time():
            SESSIONS.pop(token, None); return None
        return session["uid"]

    def current_username(self):
        raw = self.headers.get("Cookie", "")
        token = next((x.split("=",1)[1] for x in raw.split(";") if x.strip().startswith(COOKIE+"=")), "")
        session = SESSIONS.get(token)
        if not session or session["expires"] < time.time():
            return None
        return session["username"]

    def do_GET(self):
        path = urlparse(self.path).path
        if path.startswith("/api/"):
            if path == "/api/me":
                uid = self.current_user()
                self.send_json({"user": self.current_username()} if uid else {"user": None})
                return
            uid = self.current_user()
            if not uid: self.send_json({"error":"请先登录"}, 401); return
            if path == "/api/data":
                self.send_json(read_json(user_file(uid), {"transactions":[], "marks":{}})); return
            self.send_json({"error":"未找到"}, 404); return
        target = PUBLIC / ("index.html" if path == "/" else path.lstrip("/"))
        try:
            if not target.resolve().is_relative_to(PUBLIC.resolve()) or not target.is_file(): raise FileNotFoundError
            payload = target.read_bytes()
            types = {".html":"text/html; charset=utf-8", ".js":"text/javascript; charset=utf-8", ".css":"text/css; charset=utf-8", ".svg":"image/svg+xml"}
            self.send_response(200); self.send_header("Content-Type", types.get(target.suffix, "application/octet-stream")); self.send_header("Content-Length",str(len(payload))); self.end_headers(); self.wfile.write(payload)
        except (OSError, ValueError): self.send_error(404)

    def do_POST(self):
        path = urlparse(self.path).path
        try: data = self.body()
        except Exception as e: self.send_json({"error":str(e)},400); return
        if path == "/api/register" or path == "/api/login":
            username = str(data.get("username", "")).strip().lower()
            password = str(data.get("password", ""))
            if len(username) < 3 or len(username) > 40 or not username.replace("_", "").replace("-", "").isalnum() or len(password) < 8:
                self.send_json({"error":"用户名至少 3 位（仅字母、数字、_、-），密码至少 8 位"},400); return
            users_path = DATA / "users.json"; users = read_json(users_path, {})
            if path.endswith("register"):
                if username in users: self.send_json({"error":"这个用户名已被使用"},409); return
                salt = secrets.token_bytes(16)
                users[username] = {"salt":salt.hex(), "hash":hashlib.pbkdf2_hmac("sha256",password.encode(),salt,240000).hex(), "username":username}
                write_json(users_path, users)
                uid = hashlib.sha256(username.encode()).hexdigest()[:24]
                write_json(user_file(uid), {"transactions":[], "marks":{}})
            else:
                u = users.get(username)
                if not u or not hmac.compare_digest(hashlib.pbkdf2_hmac("sha256",password.encode(),bytes.fromhex(u["salt"]),240000).hex(),u["hash"]):
                    self.send_json({"error":"用户名或密码不正确"},401); return
                uid = hashlib.sha256(username.encode()).hexdigest()[:24]
            token=secrets.token_urlsafe(32); SESSIONS[token]={"uid":uid,"username":username,"expires":time.time()+60*60*24*14}
            secure = "; Secure" if os.environ.get("COINLOG_SECURE_COOKIE", "").lower() == "true" else ""
            self.send_json({"user":username},headers={"Set-Cookie":f"{COOKIE}={token}; HttpOnly; SameSite=Lax; Path=/; Max-Age=1209600{secure}"}); return
        if path == "/api/logout":
            uid=self.current_user(); raw=self.headers.get("Cookie",""); token=next((x.split("=",1)[1] for x in raw.split(";") if x.strip().startswith(COOKIE+"=")),""); SESSIONS.pop(token,None)
            self.send_json({"ok":True},headers={"Set-Cookie":f"{COOKIE}=; HttpOnly; SameSite=Lax; Path=/; Max-Age=0"}); return
        uid = self.current_user()
        if not uid: self.send_json({"error":"请先登录"},401); return
        if path == "/api/data":
            tx=data.get("transactions"); marks=data.get("marks",{})
            if not isinstance(tx,list) or len(tx)>100000 or not isinstance(marks,dict): self.send_json({"error":"记录格式不正确"},400); return
            write_json(user_file(uid), {"transactions":tx,"marks":marks})
            self.send_json({"ok":True}); return
        self.send_json({"error":"未找到"},404)

def main():
    initialize_database()
    host=os.environ.get("COINLOG_HOST","127.0.0.1"); port=int(os.environ.get("PORT", os.environ.get("COINLOG_PORT","8000")))
    print(f"CoinLog 正在运行: http://{host}:{port}")
    ThreadingHTTPServer((host,port),Handler).serve_forever()

if __name__ == "__main__": main()
