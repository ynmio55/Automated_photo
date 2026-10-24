# Automated Photo

Free, self-hosted tethered photo delivery MVP. Take a photo -> gPhoto2 transfers it to a laptop -> Pillow processes JPEG -> optional Cloudflare R2 upload -> guests scan an event QR and download pictures.

No OpenAI/Gemini API or paid image AI is required.

## Features

- Watch `incoming/` for new JPEG/PNG photos (works with manual copy as well as compatible tethered cameras)
- Automatic JPEG processing (EXIF orientation, exposure/contrast/color preset, resized web previews)
- Keep original files untouched; originals remain on the camera or in `incoming/`
- Optional private Cloudflare R2 storage via S3-compatible API; retry failed uploads on the next processing cycle
- Guest gallery with an unguessable event token, ZIP-free individual downloads and event QR
- Local development without cloud credentials

## Setup (Fedora or other Linux)

```bash
sudo dnf install python3 python3-pip gphoto2
git clone https://github.com/ynmio55/Automated_photo.git
cd Automated_photo
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python app.py
```

Visit `http://127.0.0.1:8000/admin`. Drop a JPEG or PNG in `incoming/`; it appears in the guest gallery after processing. Preview the QR at `http://127.0.0.1:8000/qr`.

### Camera tethering

Connect a camera by USB and check compatibility:
```bash
gphoto2 --auto-detect
gphoto2 --summary
```
While `python app.py` is running, in another terminal:
```bash
mkdir -p incoming
gphoto2 --capture-tethered --filename 'incoming/%Y%m%d-%H%M%S-%n.%C'
```
Not all models support capture-tethered or PC transfer while pressing the camera shutter. Some cameras require a different capture mode. Check gPhoto2 compatibility for your exact camera.

### Let guests scan

Set `PUBLIC_BASE_URL` in `.env` to an HTTPS URL that reaches this app from guests' phones (a tunnel, reverse proxy, or your deployed server). A localhost QR will only work on the laptop. Restart the server. Print the QR from `/qr`.

### Optional Cloudflare R2

Create a **private** R2 bucket and scoped API credentials. Put them in `.env`:
```dotenv
R2_ENDPOINT=https://YOUR_ACCOUNT_ID.r2.cloudflarestorage.com
R2_BUCKET=your-private-bucket
R2_ACCESS_KEY_ID=...
R2_SECRET_ACCESS_KEY=...
```
Processed photos are uploaded to R2. Downloads use short-lived signed URLs. Local copies are retained. This prototype expects credentials to be configured before starting; failed uploads are retried. Free-tier limits may change.

## Configuration

See `.env.example`. Supported inputs in this MVP: JPEG, PNG. RAW/HEIC are **not** supported yet. Install a dedicated RAW converter such as darktable for a later phase.

## Important limitations

This is a prototype, not a finished production event service. The guest QR is a **bearer link**: anyone with it can view/download the whole event album. No face recognition, customer-specific access controls, payment, admin login, automatic deletes, background job database or multi-event administration yet. Keep the app behind an HTTPS reverse proxy, protect `/admin` at the proxy before exposing publicly, and obtain consent before sharing identifiable photos.

Photos stay in `incoming/`, `processed/` and optionally your private R2 bucket. Back up originals independently. Do not commit `.env` or real customer photos.
