"""Automated Photo MVP: local tether folder -> auto JPEG processing -> QR gallery."""
from __future__ import annotations

import io
import hashlib
import os
import secrets
import threading
import time
from pathlib import Path
from urllib.parse import quote

import boto3
import qrcode
from botocore.config import Config
from dotenv import load_dotenv
from flask import Flask, abort, redirect, render_template_string, request, send_file, send_from_directory
from PIL import Image, ImageEnhance, ImageOps, UnidentifiedImageError

load_dotenv()
ROOT = Path(__file__).resolve().parent
INCOMING = ROOT / "incoming"
OUTPUT = ROOT / "processed"
for folder in (INCOMING, OUTPUT):
    folder.mkdir(exist_ok=True)

SECRET_FILE = ROOT / ".secret_event_token"
if SECRET_FILE.exists():
    TOKEN = SECRET_FILE.read_text(encoding="utf-8").strip()
else:
    TOKEN = secrets.token_urlsafe(28)
    SECRET_FILE.write_text(TOKEN, encoding="utf-8")
    try:
        SECRET_FILE.chmod(0o600)
    except OSError:
        pass

app = Flask(__name__)
TITLE = os.getenv("EVENT_TITLE", "Photo Event")
PRESET = os.getenv("PRESET", "natural").lower()
JPEG_QUALITY = max(65, min(95, int(os.getenv("JPEG_QUALITY", "90"))))
ALLOWED = {".jpg", ".jpeg", ".png"}
_done: dict[str, tuple[int, int]] = {}
_pending_upload: set[str] = set()
_lock = threading.Lock()


def r2_client():
    required = ["R2_ENDPOINT", "R2_BUCKET", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY"]
    if not all(os.getenv(v) for v in required):
        return None
    return boto3.client(
        "s3",
        endpoint_url=os.environ["R2_ENDPOINT"],
        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
        region_name="auto",
        config=Config(signature_version="s3v4"),
    )


def process_image(source: Path, destination: Path) -> None:
    with Image.open(source) as opened:
        opened.load()
        img = ImageOps.exif_transpose(opened)
        if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
            bg = Image.new("RGB", img.size, "white")
            rgba = img.convert("RGBA")
            bg.paste(rgba, mask=rgba.getchannel("A"))
            img = bg
        else:
            img = img.convert("RGB")
        if PRESET == "warm":
            img = ImageEnhance.Color(img).enhance(1.10)
            img = ImageEnhance.Contrast(img).enhance(1.07)
            red, green, blue = img.split()
            red = red.point(lambda v: min(255, int(v * 1.035)))
            blue = blue.point(lambda v: int(v * 0.97))
            img = Image.merge("RGB", (red, green, blue))
        elif PRESET == "vivid":
            img = ImageEnhance.Color(img).enhance(1.20)
            img = ImageEnhance.Contrast(img).enhance(1.10)
        else:
            img = ImageEnhance.Contrast(img).enhance(1.06)
            img = ImageEnhance.Color(img).enhance(1.05)
        tmp = destination.with_suffix(".tmp")
        img.save(tmp, format="JPEG", quality=JPEG_QUALITY, optimize=True)
        tmp.replace(destination)


def send_to_r2(filename: str) -> bool:
    client = r2_client()
    if client is None:
        return False
    try:
        client.upload_file(
            str(OUTPUT / filename),
            os.environ["R2_BUCKET"],
            f"events/{TOKEN}/{filename}",
            ExtraArgs={"ContentType": "image/jpeg"},
        )
        return True
    except Exception as exc:
        app.logger.warning("R2 upload failed (%s): %s", filename, exc)
        return False


def scan_once() -> None:
    """Poll sizes twice before processing to avoid reading partial camera transfers."""
    for source in sorted(INCOMING.iterdir()):
        if not source.is_file() or source.suffix.lower() not in ALLOWED:
            continue
        try:
            stat = source.stat()
            signature = (stat.st_size, stat.st_mtime_ns)
            if stat.st_size == 0:
                continue
            key = source.name
            if _done.get(key) != signature:
                # Stable transfer: size/mtime must not change across one polling interval.
                time.sleep(0.25)
                if (source.stat().st_size, source.stat().st_mtime_ns) != signature:
                    continue
                fingerprint = hashlib.sha256(f"{source.name}:{signature}".encode()).hexdigest()[:12]
                name = f"{source.stem}-{fingerprint}.jpg"
                dest = OUTPUT / name
                try:
                    if not dest.exists():
                        process_image(source, dest)
                except (UnidentifiedImageError, OSError, ValueError) as exc:
                    app.logger.warning("Cannot process %s: %s", source.name, exc)
                    continue
                _done[key] = signature
                if r2_client() is not None:
                    _pending_upload.add(name)
                app.logger.info("Processed %s -> %s", source.name, name)
        except FileNotFoundError:
            continue
    for name in tuple(_pending_upload):
        if send_to_r2(name):
            _pending_upload.discard(name)


def worker() -> None:
    while True:
        try:
            with _lock:
                scan_once()
        except Exception:
            app.logger.exception("Background worker failed")
        time.sleep(2)


GALLERY = """<!doctype html><html lang="th"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{{ title }}</title><style>
body{margin:0;font:16px system-ui;background:#101217;color:#f4f4f5}
header{padding:30px max(20px,5vw);border-bottom:1px solid #333}
h1{margin:0 0 8px}.muted{color:#a1a1aa}
main{padding:24px max(20px,5vw)}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:16px}
.photo{background:#20222a;border:1px solid #32343c;border-radius:12px;overflow:hidden}
.photo img{width:100%;height:210px;object-fit:cover;display:block}
.photo a{display:block;text-align:center;padding:12px;color:white;text-decoration:none}
.photo a:hover{background:#333842}</style></head><body>
<header><h1>{{ title }}</h1><div class="muted">เลือกภาพเพื่อดาวน์โหลด · {{ photos|length }} ภาพ</div></header>
<main><div class="grid">{% for name in photos %}
<div class="photo"><img loading="lazy" src="{{ url_for('preview',token=token,name=name) }}" alt="Photo">
<a href="{{ url_for('download',token=token,name=name) }}">ดาวน์โหลดภาพ</a></div>
{% endfor %}</div>{% if not photos %}<p class="muted">ยังไม่มีภาพ ลองเปิดหน้านี้อีกครั้งเมื่อช่างภาพอัปโหลดแล้ว</p>{% endif %}</main>
</body></html>"""


def check_token(token: str) -> None:
    if not secrets.compare_digest(token, TOKEN):
        abort(404)


def get_photo(name: str) -> Path:
    if not name.endswith(".jpg") or Path(name).name != name:
        abort(404)
    item = OUTPUT / name
    if not item.is_file():
        abort(404)
    return item


@app.get("/")
def home():
    return redirect("/admin")


@app.get("/admin")
def admin():
    # Local only; protect at reverse proxy if exposed beyond localhost.
    photos = len(list(OUTPUT.glob("*.jpg")))
    return (
        f"<h2>Automated Photo</h2><p>{photos} processed photos.</p>"
        f"<p>Copy JPEG/PNG files to: <code>{INCOMING}</code></p>"
        f"<p><a href='/qr'>Open event QR</a></p>"
        "<p>Note: /admin has no login; do not expose it on the public Internet.</p>"
    )


@app.get("/qr")
def qr_page():
    base = os.getenv("PUBLIC_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
    link = f"{base}/event/{TOKEN}"
    return f"""<!doctype html><meta charset="utf-8"><title>Event QR</title>
    <style>body{{font:16px system-ui;text-align:center;padding:32px}}</style>
    <h1>Scan to see your photos</h1><img src="/qr.png" width="280" height="280">
    <p><a href="{link}">Open guest gallery</a></p>
    <p>Set PUBLIC_BASE_URL to your reachable HTTPS address before printing.</p>"""


@app.get("/qr.png")
def qr_png():
    url = os.getenv("PUBLIC_BASE_URL", "http://127.0.0.1:8000").rstrip("/") + f"/event/{TOKEN}"
    image = qrcode.make(url)
    bio = io.BytesIO()
    image.save(bio, format="PNG")
    bio.seek(0)
    return send_file(bio, mimetype="image/png")


@app.get("/event/<token>")
def gallery(token):
    check_token(token)
    photos = sorted((p.name for p in OUTPUT.glob("*.jpg")), reverse=True)
    return render_template_string(GALLERY, title=TITLE, token=TOKEN, photos=photos)


@app.get("/preview/<token>/<name>")
def preview(token, name):
    check_token(token)
    return send_from_directory(OUTPUT, get_photo(name).name, mimetype="image/jpeg", max_age=30)


@app.get("/download/<token>/<name>")
def download(token, name):
    check_token(token)
    photo = get_photo(name)
    if r2_client() is not None and name not in _pending_upload:
        try:
            url = r2_client().generate_presigned_url(
                "get_object",
                Params={"Bucket": os.environ["R2_BUCKET"], "Key": f"events/{TOKEN}/{name}",
                        "ResponseContentDisposition": f'attachment; filename="{quote(name)}"'},
                ExpiresIn=3600,
            )
            return redirect(url)
        except Exception:
            app.logger.exception("R2 presigned download failed; falling back to local")
    return send_from_directory(OUTPUT, photo.name, as_attachment=True, download_name=name)


if __name__ == "__main__":
    threading.Thread(target=worker, daemon=True).start()
    app.run(host=os.getenv("HOST", "127.0.0.1"), port=int(os.getenv("PORT", "8000")), threaded=True)
