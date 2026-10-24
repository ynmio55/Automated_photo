"""Hybrid MVP: multi-user photo events and camera-folder ingestion.

Run: python hybrid.py
No paid AI APIs. SQLite, Flask, Pillow; optional desktop agent uses /api/ingest.
"""
import io
import os
import re
import secrets
import sqlite3
import threading
import time
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path

import qrcode
from dotenv import load_dotenv
from flask import Flask, abort, jsonify, redirect, render_template_string, request, send_file, session, url_for
from PIL import Image, ImageEnhance, ImageOps, UnidentifiedImageError
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

load_dotenv()
BASE = Path(__file__).resolve().parent
STORE = BASE / "hybrid_data"
STORE.mkdir(exist_ok=True)
DB = STORE / "app.sqlite"
app = Flask(__name__)
key_file = STORE / ".flask_secret"
if not key_file.exists():
    key_file.write_text(secrets.token_hex(32), encoding="ascii")
    key_file.chmod(0o600)
app.secret_key = os.getenv("APP_SECRET_KEY") or key_file.read_text().strip()
app.config.update(MAX_CONTENT_LENGTH=35 * 1024 * 1024, SESSION_COOKIE_HTTPONLY=True,
                  SESSION_COOKIE_SAMESITE="Lax",
                  SESSION_COOKIE_SECURE=os.getenv("HTTPS_ONLY", "0") == "1")
EXT = {".jpg", ".jpeg", ".png"}
mutex = threading.Lock()


def connect():
    c = sqlite3.connect(DB, timeout=20)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    return c


def init_db():
    with connect() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS users(
            id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS events(
            id INTEGER PRIMARY KEY, owner_id INTEGER NOT NULL REFERENCES users(id),
            title TEXT NOT NULL, token TEXT UNIQUE NOT NULL,
            ingest_key TEXT UNIQUE NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS photos(
            id INTEGER PRIMARY KEY, event_id INTEGER NOT NULL REFERENCES events(id),
            filename TEXT NOT NULL, uploaded_at TEXT NOT NULL,
            UNIQUE(event_id,filename));
        """)


init_db()

STYLE = """<style>
*{box-sizing:border-box}body{font-family:system-ui,-apple-system,sans-serif;background:#101116;color:#ececf1;margin:0}
nav{display:flex;gap:18px;align-items:center;padding:20px max(20px,6vw);border-bottom:1px solid #292a32}
nav a{color:#cbd5e1;text-decoration:none}nav strong{margin-right:auto;font-size:20px}
main{max-width:1050px;margin:48px auto;padding:0 22px}h1{font-size:34px;margin:0 0 12px}
p,.sub{color:#a1a1aa}a{color:#8db8ff}input,button{font:inherit;border-radius:10px;padding:12px 14px}
input{background:#1b1d26;border:1px solid #454653;color:#fff;width:100%;margin:7px 0 16px}
button,.btn{display:inline-block;background:#d9eefc;color:#101116;border:0;font-weight:650;cursor:pointer;text-decoration:none;border-radius:10px;padding:12px 18px}
.card{background:#191b23;border:1px solid #333640;border-radius:18px;padding:22px;margin:18px 0}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:16px}
.photo img{width:100%;height:210px;object-fit:cover;border-radius:12px}
.photo a{display:block;padding:10px}label{font-weight:600}.error{color:#ff9c9c}
small{color:#9297a4}code{word-break:break-all}
</style>"""
SHELL = """<!doctype html><html lang="th"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{{ title }} · Automated Photo</title>""" + STYLE + """</head><body>
<nav><strong>◉ Automated Photo</strong>
<a href="{{ url_for('dashboard') }}">Dashboard</a>
{% if session.get('uid') %}<a href="{{ url_for('logout') }}">ออกจากระบบ</a>{% else %}
<a href="{{ url_for('login') }}">เข้าสู่ระบบ</a><a href="{{ url_for('register') }}">สมัครสมาชิก</a>{% endif %}</nav>
<main>{{ body|safe }}</main></body></html>"""


def render(title, body, **kwargs):
    html = render_template_string(body, **kwargs)
    return render_template_string(SHELL, title=title, body=html)


def logged_in(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("uid"):
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped


def owns_event(event_id):
    with connect() as c:
        event = c.execute("SELECT * FROM events WHERE id=? AND owner_id=?",
                          (event_id, session.get("uid"))).fetchone()
    if event is None:
        abort(404)
    return dict(event)


def process(data, destination):
    with Image.open(io.BytesIO(data)) as source:
        source.load()
        if source.width * source.height > 80_000_000:
            raise ValueError("Image exceeds pixel limit")
        img = ImageOps.exif_transpose(source).convert("RGB")
        img = ImageEnhance.Contrast(img).enhance(1.06)
        img = ImageEnhance.Color(img).enhance(1.05)
        img.thumbnail((4500, 4500))
        img.save(destination, format="JPEG", quality=90, optimize=True)


def save_photo(event_id, original_name, payload):
    ext = Path(original_name).suffix.lower()
    if ext not in EXT:
        raise ValueError("Only JPEG and PNG supported")
    if len(payload) > 34 * 1024 * 1024:
        raise ValueError("File too large")
    folder = STORE / str(event_id)
    folder.mkdir(exist_ok=True)
    filename = secrets.token_hex(12) + ".jpg"
    dest = folder / filename
    try:
        process(payload, dest)
    except Exception:
        dest.unlink(missing_ok=True)
        raise
    with connect() as c:
        c.execute("INSERT INTO photos(event_id, filename, uploaded_at) VALUES (?,?,?)",
                  (event_id, filename, datetime.now(timezone.utc).isoformat()))
    return filename


@app.route("/")
def home():
    return redirect(url_for("dashboard"))


@app.route("/register", methods=["GET", "POST"])
def register():
    error = ""
    if request.method == "POST":
        username = request.form.get("username", "").strip().lower()
        password = request.form.get("password", "")
        if not re.fullmatch(r"[a-z0-9_]{3,30}", username):
            error = "ชื่อผู้ใช้ต้องมี 3–30 ตัว ใช้ a-z, 0-9, _"
        elif len(password) < 12:
            error = "รหัสผ่านต้องมีอย่างน้อย 12 ตัว"
        else:
            try:
                with connect() as c:
                    cursor = c.execute("INSERT INTO users(username,password_hash,created_at) VALUES (?,?,?)",
                                       (username, generate_password_hash(password),
                                        datetime.now(timezone.utc).isoformat()))
                    user_id = cursor.lastrowid
                session.clear()
                session["uid"] = user_id
                return redirect(url_for("dashboard"))
            except sqlite3.IntegrityError:
                error = "ชื่อผู้ใช้นี้ถูกใช้แล้ว"
    return render("สมัครสมาชิก", """<h1>สมัครสมาชิกช่างภาพ</h1><p>บัญชีสำหรับเจ้าของงานถ่ายภาพ</p>
    <div class="card" style="max-width:480px"><form method="post"><label>Username</label>
    <input name="username" autocomplete="username" required minlength="3" maxlength="30">
    <label>Password (12 ตัวขึ้นไป)</label><input type="password" name="password" autocomplete="new-password" minlength="12" required>
    <p class="error">{{ error }}</p><button>สร้างบัญชี</button></form></div>""", error=error)


@app.route("/login", methods=["GET", "POST"])
def login():
    error = ""
    if request.method == "POST":
        with connect() as c:
            user = c.execute("SELECT * FROM users WHERE username=?",
                             (request.form.get("username", "").strip().lower(),)).fetchone()
        if user and check_password_hash(user["password_hash"], request.form.get("password", "")):
            session.clear()
            session["uid"] = user["id"]
            return redirect(url_for("dashboard"))
        error = "ชื่อผู้ใช้หรือรหัสผ่านไม่ถูกต้อง"
    return render("เข้าสู่ระบบ", """<h1>เข้าสู่ระบบ</h1><div class="card" style="max-width:480px">
    <form method="post"><label>Username</label><input name="username" autocomplete="username" required>
    <label>Password</label><input name="password" type="password" autocomplete="current-password" required>
    <p class="error">{{ error }}</p><button>เข้าสู่ระบบ</button></form></div>""", error=error)


@app.get("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/dashboard", methods=["GET", "POST"])
@logged_in
def dashboard():
    if request.method == "POST":
        title = request.form.get("title", "").strip()[:120]
        if title:
            with connect() as c:
                c.execute("INSERT INTO events(owner_id,title,token,ingest_key,created_at) VALUES (?,?,?,?,?)",
                          (session["uid"], title, secrets.token_urlsafe(24),
                           secrets.token_urlsafe(32), datetime.now(timezone.utc).isoformat()))
        return redirect(url_for("dashboard"))
    with connect() as c:
        events = [dict(x) for x in c.execute("SELECT * FROM events WHERE owner_id=? ORDER BY id DESC",
                                             (session["uid"],))]
    return render("Dashboard", """<h1>งานถ่ายภาพของคุณ</h1><p>สร้างงานใหม่และรับภาพจากกล้องผ่าน Desktop Agent</p>
    <div class="card"><form method="post"><label>ชื่องานใหม่</label><input name="title" placeholder="Graduation 2026" required maxlength="120">
    <button>+ สร้างงาน</button></form></div>
    <div class="grid">{% for event in events %}<div class="card"><h3>{{ event.title }}</h3>
    <p><small>Event #{{ event.id }}</small></p>
    <a class="btn" href="{{ url_for('manage_event',event_id=event.id) }}">จัดการงาน →</a></div>{% endfor %}</div>
    """, events=events)


@app.route("/manage/<int:event_id>", methods=["GET", "POST"])
@logged_in
def manage_event(event_id):
    event = owns_event(event_id)
    error = ""
    if request.method == "POST":
        f = request.files.get("photo")
        if f:
            try:
                save_photo(event_id, secure_filename(f.filename or ""), f.read())
                return redirect(url_for("manage_event", event_id=event_id))
            except (ValueError, UnidentifiedImageError, OSError) as exc:
                error = str(exc)
    base = os.getenv("PUBLIC_BASE_URL", request.url_root.rstrip("/")).rstrip("/")
    guest_url = base + url_for("guest_gallery", token=event["token"])
    with connect() as c:
        count = c.execute("SELECT COUNT(*) FROM photos WHERE event_id=?", (event_id,)).fetchone()[0]
    return render("จัดการงาน", """<h1>{{ event.title }}</h1><p>มีภาพแล้ว {{ count }} ภาพ</p>
    <div class="card"><h3>อัปโหลดภาพทดสอบ</h3>
    <form method="post" enctype="multipart/form-data"><input type="file" name="photo" accept="image/jpeg,image/png" required>
    <p class="error">{{ error }}</p><button>อัปโหลดภาพ</button></form></div>
    <div class="card"><h3>QR สำหรับลูกค้า</h3><img src="{{ url_for('qr_event',event_id=event.id) }}" width="220" height="220">
    <p><a href="{{ guest_url }}">{{ guest_url }}</a></p></div>
    <div class="card"><h3>ตั้งค่า Desktop Agent</h3><p>ใช้ API Key นี้เฉพาะกับงานนี้ ห้ามเปิดเผยต่อผู้อื่น</p>
    <code>{{ event.ingest_key }}</code><p>Event ID: <code>{{ event.id }}</code></p>
    <small>ใช้ตัวแปร PHOTO_SERVER, PHOTO_EVENT_ID และ PHOTO_INGEST_KEY ในเครื่องช่างภาพ</small></div>
    """, event=event, count=count, guest_url=guest_url, error=error)


@app.get("/qr/<int:event_id>")
@logged_in
def qr_event(event_id):
    event = owns_event(event_id)
    link = os.getenv("PUBLIC_BASE_URL", request.url_root.rstrip("/")).rstrip("/") + url_for(
        "guest_gallery", token=event["token"])
    png = io.BytesIO()
    qrcode.make(link).save(png, format="PNG")
    png.seek(0)
    return send_file(png, mimetype="image/png")


@app.get("/g/<token>")
def guest_gallery(token):
    with connect() as c:
        event = c.execute("SELECT * FROM events WHERE token=?", (token,)).fetchone()
        if not event:
            abort(404)
        photos = [dict(x) for x in c.execute(
            "SELECT * FROM photos WHERE event_id=? ORDER BY id DESC", (event["id"],))]
    return render(event["title"], """<h1>{{ title }}</h1><p>{{ photos|length }} ภาพ · สแกน QR เพื่อเปิดแกลเลอรี</p>
    <div class="grid">{% for photo in photos %}<div class="card photo">
    <a href="{{ url_for('guest_photo', token=token, photo_id=photo.id) }}">
    <img loading="lazy" src="{{ url_for('guest_photo',token=token,photo_id=photo.id) }}" alt="Photo"></a>
    <a href="{{ url_for('guest_photo',token=token,photo_id=photo.id) }}?download=1">ดาวน์โหลดภาพ</a>
    </div>{% endfor %}</div>""", title=event["title"], photos=photos, token=token)


@app.get("/g/<token>/photo/<int:photo_id>")
def guest_photo(token, photo_id):
    with connect() as c:
        row = c.execute("""SELECT p.*, e.token FROM photos p JOIN events e ON e.id=p.event_id
                           WHERE p.id=? AND e.token=?""", (photo_id, token)).fetchone()
    if not row:
        abort(404)
    return send_file(STORE / str(row["event_id"]) / row["filename"],
                     mimetype="image/jpeg", as_attachment=request.args.get("download") == "1")


@app.post("/api/ingest/<int:event_id>")
def api_ingest(event_id):
    supplied = request.headers.get("X-Photo-Key", "")
    with connect() as c:
        event = c.execute("SELECT ingest_key FROM events WHERE id=?", (event_id,)).fetchone()
    if not event or not secrets.compare_digest(supplied, event["ingest_key"]):
        abort(401)
    f = request.files.get("photo")
    if not f:
        return jsonify(error="Missing photo"), 400
    try:
        name = save_photo(event_id, secure_filename(f.filename or ""), f.read())
    except (ValueError, UnidentifiedImageError, OSError) as exc:
        return jsonify(error=str(exc)), 400
    return jsonify(filename=name), 201


if __name__ == "__main__":
    app.run(host=os.getenv("HOST", "127.0.0.1"), port=int(os.getenv("PORT", "8000")), threaded=True)
