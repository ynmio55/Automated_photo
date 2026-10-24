# Hybrid MVP: photographer accounts + desktop upload + guest QR

This is the **new hybrid prototype**, separate from the original `app.py` proof of concept. It is built with Flask + SQLite + Tkinter. A future production version can replace the desktop shell with Tauri.

## Fedora setup

```bash
sudo dnf install python3 python3-tkinter gphoto2
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install requests
cp .env.example .env
python hybrid.py
```

Open http://127.0.0.1:8000/register, create a photographer account, log in and create a photo event. Open the event management page to get its Event ID and ingestion key.

In `.env`, configure:

```dotenv
PHOTO_SERVER=http://127.0.0.1:8000
PHOTO_EVENT_ID=1
PHOTO_INGEST_KEY=copy-key-from-event-screen
PHOTO_WATCH_DIR=incoming
```

On the **same machine** and from a separate terminal, start the desktop agent:

```bash
source .venv/bin/activate
python desktop_agent.py
```

Click **Start automatic upload** and copy a JPEG into `incoming/` to test.

For supported cameras, in another terminal:

```bash
gphoto2 --auto-detect
gphoto2 --capture-tethered --filename 'incoming/%Y%m%d-%H%M%S-%n.%C'
```

Note: camera support varies. Only JPEG/PNG images are currently accepted. If the camera produces RAW files, configure RAW+JPEG capture and send the JPEG here; RAW processing comes later.

For phone QR access, set `PUBLIC_BASE_URL` to a reachable HTTPS URL. Using localhost in a QR only works locally.

## Security and production limitations

- New accounts use password hashes and session cookies; this version lacks email verification, password reset, CSRF defenses, account rate limiting, and admin permissions.
- `/register` is publicly accessible if deployed; keep the prototype local until those protections are implemented.
- Event QR URLs are secret bearer links: anyone possessing a link can access **all** photos for that event; obtain consent and consider PIN-gated/private delivery.
- Ingest keys appear only in the event owner's management page. Treat them as passwords; this prototype cannot rotate them.
- Uploads are limited to 35 MB and 80 megapixels, but production needs stronger image validation, resource isolation, HTTPS, persistent upload queues and storage quotas.
- Photos are stored locally under `hybrid_data/`, not uploaded to R2 yet. Original `app.py` has separate optional R2 support. Do not expose `hybrid_data/` through a static file server.
- Always keep originals/backups and do not commit runtime data or secrets.

## Architecture

Camera -> gPhoto2 -> incoming folder -> Tkinter desktop agent -> authenticated HTTP upload -> Flask photo pipeline -> local storage/SQLite -> guest gallery/QR.

The design runs without OpenAI/Gemini APIs or cloud accounts during development.
