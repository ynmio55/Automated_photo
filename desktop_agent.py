"""Local desktop agent for tether-folder monitoring and authenticated upload.

Run alongside hybrid.py. Compatible cameras can save JPEGs through gPhoto2.
"""
import os
import threading
import time
from pathlib import Path
from tkinter import Button, Label, StringVar, Tk

import requests
from dotenv import load_dotenv

load_dotenv()
FOLDER = Path(os.getenv("PHOTO_WATCH_DIR", "incoming")).resolve()
FOLDER.mkdir(parents=True, exist_ok=True)
SERVER = os.getenv("PHOTO_SERVER", "http://127.0.0.1:8000").rstrip("/")
EVENT_ID = os.getenv("PHOTO_EVENT_ID", "")
KEY = os.getenv("PHOTO_INGEST_KEY", "")
seen = {}
stop = threading.Event()


def run(status):
    if not EVENT_ID or not KEY:
        status.set("กรุณาตั้งค่า PHOTO_EVENT_ID และ PHOTO_INGEST_KEY ใน .env")
        return
    status.set(f"Watching {FOLDER}")
    while not stop.is_set():
        for file in FOLDER.iterdir():
            if not file.is_file() or file.suffix.lower() not in (".jpg", ".jpeg", ".png"):
                continue
            try:
                a = file.stat()
                signature = (a.st_size, a.st_mtime_ns)
                if not a.st_size or seen.get(str(file)) == signature:
                    continue
                time.sleep(0.6)
                b = file.stat()
                if (b.st_size, b.st_mtime_ns) != signature:
                    continue
                # Successfully uploaded photos are remembered for this session only.
                with file.open("rb") as fh:
                    response = requests.post(
                        f"{SERVER}/api/ingest/{EVENT_ID}",
                        headers={"X-Photo-Key": KEY},
                        files={"photo": (file.name, fh, "application/octet-stream")},
                        timeout=90,
                    )
                response.raise_for_status()
                seen[str(file)] = signature
                status.set(f"Uploaded: {file.name}")
            except Exception as exc:
                status.set(f"Retrying: {file.name} ({type(exc).__name__})")
        stop.wait(2)


def main():
    root = Tk()
    root.title("Automated Photo — Desktop Agent")
    root.geometry("550x220")
    root.configure(padx=24, pady=20)
    Label(root, text="Automated Photo", font=("Sans", 20, "bold")).pack(pady=6)
    Label(root, text=f"Folder: {FOLDER}", wraplength=490).pack(pady=4)
    status = StringVar(value="Ready to start")
    Label(root, textvariable=status, wraplength=490).pack(pady=10)
    worker = [None]

    def start():
        if worker[0] and worker[0].is_alive():
            return
        stop.clear()
        worker[0] = threading.Thread(target=run, args=(status,), daemon=True)
        worker[0].start()

    Button(root, text="Start automatic upload", command=start).pack()
    root.protocol("WM_DELETE_WINDOW", lambda: (stop.set(), root.destroy()))
    root.mainloop()


if __name__ == "__main__":
    main()
