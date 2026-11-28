"""Everything the class demo needs, in one command: the app over HTTPS for phones.

    start.bat            phone check-in (double-click it, or run it from Command Prompt)
    start.bat kiosk      door camera

1. makes sure the database is up to date and the ArcFace models are downloaded
2. opens a Cloudflare quick tunnel and prints its https:// address, with a QR code for phones
3. runs the app with production settings, and opens the Classes page on this laptop

Press Ctrl+C to stop; the tunnel closes with it, and its address stops working.
"""

import argparse
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PORT = 5000
# a production key kept between runs, so people stay signed in when the app restarts.
# gitignored: anyone with it could forge a login cookie.
KEY_FILE = ROOT / ".flask_secret_prod"


def find_cloudflared():
    """The cloudflared program: installed with winget, or saved in the project folder."""
    found = shutil.which("cloudflared") or shutil.which(str(ROOT / "cloudflared"))
    if not found:
        sys.exit("cloudflared is not installed. Install it with:\n"
                 "    winget install --id Cloudflare.cloudflared\n"
                 "then open a new Command Prompt and try again.")
    return found


def show_address(url):
    """Print the tunnel address big enough to read, plus a QR code phones can scan to open it."""
    import qrcode  # already installed: it draws the classroom QR

    print("\n" + "=" * 70)
    print(f"  Phones: open  {url}")
    print("  (or scan this QR code with the phone's camera)")
    print("=" * 70)
    qr = qrcode.QRCode(border=2)
    qr.add_data(url)
    qr.print_ascii(invert=True)
    print("Teacher screen: opening the Classes page in your browser.")
    print("Press Ctrl+C here to stop.\n")


def watch_tunnel(tunnel):
    """Read cloudflared's output; once the tunnel is connected, show its address."""
    url = None
    for line in tunnel.stderr:  # cloudflared writes its log to stderr
        if url is None:
            match = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", line)
            if match:
                url = match.group(0)
        if url and "Registered tunnel connection" in line:
            show_address(url)
            time.sleep(3)  # give the new address a moment to work everywhere
            webbrowser.open(url + "/classes/")
            break
    for _ in tunnel.stderr:  # keep draining, or cloudflared stalls on a full pipe
        pass


def main():
    parser = argparse.ArgumentParser(description="Run the app for the class demo.")
    parser.add_argument("mode", nargs="?", choices=["phone", "kiosk"], default="phone")
    args = parser.parse_args()
    cloudflared = find_cloudflared()

    # 1. the database and the models: both scripts are quick when nothing needs doing
    for script in ("init_db.py", "download_arcface.py"):
        subprocess.run([sys.executable, str(ROOT / "scripts" / script)], cwd=ROOT, check=True)

    # 2. production settings, set before the app is imported because it reads them then:
    # no debugger, and the login cookie only travels over https
    if not KEY_FILE.exists():
        KEY_FILE.write_text(secrets.token_hex(32))
    os.environ["APP_ENV"] = "production"
    os.environ["FLASK_SECRET_KEY"] = KEY_FILE.read_text().strip()
    os.environ["KIOSK_MODE"] = "1" if args.mode == "kiosk" else "0"
    print(f"Mode: {'door camera (kiosk)' if args.mode == 'kiosk' else 'phone check-in'}")

    # 3. the tunnel, in the background
    print("Opening the Cloudflare tunnel...")
    tunnel = subprocess.Popen(
        [cloudflared, "tunnel", "--no-autoupdate", "--url", f"http://localhost:{PORT}"],
        stderr=subprocess.PIPE, text=True,
    )
    threading.Thread(target=watch_tunnel, args=(tunnel,), daemon=True).start()

    # 4. the app, in this window until Ctrl+C
    sys.path.insert(0, str(ROOT))
    from waitress import serve  # a proper multi-threaded server, unlike Flask's own

    from wsgi import app

    try:
        # 127.0.0.1 only: phones come in through the tunnel, never straight over the Wi-Fi.
        # cloudflared, on this computer, is the one trusted proxy: waitress takes each
        # phone's address and "this came in over https" from the headers it adds.
        serve(app, listen=f"127.0.0.1:{PORT}", threads=8, trusted_proxy="127.0.0.1",
              trusted_proxy_count=1, trusted_proxy_headers="x-forwarded-for x-forwarded-proto",
              log_untrusted_proxy_headers=False)
    except KeyboardInterrupt:
        pass
    finally:
        tunnel.terminate()
        print("Stopped. The tunnel address no longer works.")


if __name__ == "__main__":
    main()
