@echo off
rem Run the app for phones over HTTPS, through a free Cloudflare quick tunnel (Windows).
rem
rem   1. In one Command Prompt, with the venv on:   scripts\serve_https.bat
rem   2. In a second one:   cloudflared tunnel --url http://localhost:5000
rem      It prints an address like https://some-words.trycloudflare.com: open that on
rem      any phone, on any network. It changes every time cloudflared starts.
rem
rem Press Ctrl+C in both windows to stop. While they run, anyone with the address can
rem reach the sign-in page, so use strong passwords and stop both after the demo.

rem production settings: the debugger is off, and the login cookie only travels over https
set APP_ENV=production

rem a fresh random key each run: it signs the login cookie, so after a restart everyone
rem simply signs in again
for /f %%k in ('python -c "import secrets; print(secrets.token_hex(32))"') do set FLASK_SECRET_KEY=%%k

rem phone check-in unless the door camera was asked for first with:  set KIOSK_MODE=1
if not defined KIOSK_MODE set KIOSK_MODE=0

echo Starting the app on http://127.0.0.1:5000 (KIOSK_MODE=%KIOSK_MODE%)
echo Now run in another window:  cloudflared tunnel --url http://localhost:5000

rem waitress is a proper multi-threaded server; Flask's own is for development only.
rem   127.0.0.1 only: phones reach the app through the tunnel, never straight over the Wi-Fi.
rem   trusted_proxy: cloudflared, on this same computer, is the one proxy in front of the app.
rem   waitress takes each phone's address and "this came in over https" from the headers
rem   cloudflared adds, and ignores those headers from anything else.
python -c "from waitress import serve; from wsgi import app; serve(app, listen='127.0.0.1:5000', threads=8, trusted_proxy='127.0.0.1', trusted_proxy_count=1, trusted_proxy_headers='x-forwarded-for x-forwarded-proto', log_untrusted_proxy_headers=False)"
