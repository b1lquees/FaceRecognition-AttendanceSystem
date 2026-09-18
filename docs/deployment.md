# Deployment

[Deployment in the README](../README.md#deployment) gets a stack running with
`docker compose up -d`, which is the way to deploy this. This page holds the parts that do
not fit there: choosing a certificate, what the healthcheck actually promises, and running
the container by hand without compose.

- [TLS and certificates](#tls-and-certificates)
- [The healthcheck](#the-healthcheck)
- [Running by hand](#running-by-hand)
- [Backup and restore](backup-restore.md) — separate page

## TLS and certificates

The camera needs HTTPS to work at all: `getUserMedia()` is only exposed in a secure
context, so over plain HTTP to anything but `localhost` the page cannot open a camera. TLS
here is not a hardening step to do later, it is the difference between a kiosk and a blank
screen.

**Choose your certificate** in [`Caddyfile`](../Caddyfile). It ships configured for the common
case — a camera on a LAN with no public DNS name — where Caddy runs its own certificate
authority. That needs one manual step: export its root certificate and install it in the
kiosk machine's trust store.

```bash
docker compose cp caddy:/data/caddy/pki/authorities/local/root.crt .
```

> Do **not** click through the browser's certificate warning instead. An overridden
> certificate error leaves the page in a state where camera access is unreliable, which is
> the one capability this deployment exists for.

If you do have a hostname that resolves publicly with ports 80 and 443 reachable, the
`Caddyfile` has a two-line alternative that obtains a Let's Encrypt certificate on first
start and renews it indefinitely — no certbot, no cron, no reload hook.

## The healthcheck

**The app service has a healthcheck**, and it is worth knowing what it does and does not
do. Gunicorn's own `--timeout 60` already reaps a worker wedged mid request, so a hung
worker was covered before the healthcheck existed. What the arbiter cannot see is a
container that is up, responsive, and has nothing to serve — brought up without anyone
running `init_db.py`, so every page fails on its first query. That is what `/healthz`
looks for: the schema, not merely a file it can open.

> Docker's vocabulary oversells this. Marking a container **unhealthy does not restart
> it** — Compose acts on health only in `depends_on`, and restart-on-unhealthy is a Swarm
> feature. What you get is `docker compose ps` telling the truth instead of reporting
> `Up 6 days` for a container that has served nothing but errors for five of them. Check
> it, or put something in front that does.

```bash
docker compose ps
```

Caddy deliberately waits only for the app to *start*, not to become healthy. The order in
this section is `up -d` first and `init_db.py` second, so on a first deployment the app is
legitimately unhealthy for as long as it takes you to run that command — and gating the
proxy on health would mean no web server at all during the window when you most want to
see an error page.

## Running by hand

The image contains the application and nothing else. `.dockerignore` keeps the database,
the photos and the encoding cache out of the build context deliberately — face data has no
business inside an image that gets pushed to a registry — so a fresh container starts empty
and writes everything into `/data`.

**Mount something there.** Without a volume, `/data` is the container's own writable layer
and `docker rm` deletes the attendance record along with the container.

> **Use a named volume, not a bind mount.** The container runs as uid 10001, and a named
> volume is seeded with the ownership `/data` has in the image, so it is writable from the
> first second. A bind mount is not seeded with anything — `-v ./data:/data` arrives owned
> by whoever owns the host directory, and unless that is uid 10001 the first write fails
> with a permission error that looks like a bug in the application. If you need a bind
> mount anyway, `chown 10001:10001` the host directory before starting the container.

```bash
docker build -t attendance .
docker volume create attendance-data
```

Create the schema and the first administrator inside throwaway containers, on the volume
the real one will use. `--rm` deletes the container afterwards; the volume keeps what the
scripts wrote:

```bash
docker run --rm -it -v attendance-data:/data attendance python scripts/init_db.py
docker run --rm -it -v attendance-data:/data attendance python scripts/create_user.py alice --role admin
```

And a separate account for the camera station, left at the default `viewer` role — never
an administrator, for the reasons given under [Quick start](../README.md#quick-start).

```bash
docker run --rm -it -v attendance-data:/data attendance python scripts/create_user.py station
```

Then run it:

```bash
docker run -d --name attendance -p 8000:8000 -v attendance-data:/data -e FLASK_SECRET_KEY=CHANGE-ME -e TIMEZONE=Asia/Karachi -e TRUSTED_PROXY_HOPS=1 attendance
```

The key is not optional: the image sets `APP_ENV=production`, and production refuses to
start without one rather than falling back to a guessable default.

> **It will not work over plain HTTP, by design.** `APP_ENV=production` also sets
> `SESSION_COOKIE_SECURE`, so the browser is told to send the session cookie over HTTPS
> only. Over `http://localhost:8000` the login succeeds, the cookie is dropped, and the
> next page returns you to the login form as though the password were wrong. Put a
> TLS-terminating proxy in front of it; that is the fix, not disabling the flag.

> **Tell the application about that proxy.** Once there is one in front, every request
> arrives from the proxy's address, so without `TRUSTED_PROXY_HOPS` the rate limiter sees
> a single client no matter how many there are and the audit trail records one address for
> everybody. The `docker run` line above sets it to `1`, which is right for a single proxy.
> Count the hops and set the real number: too low is merely useless, but **too high is
> worse than leaving it unset**, because `X-Forwarded-For` is written by the client, so
> trusting one hop more than exists lets anyone claim any address they like.

> **On Windows, run these from PowerShell or CMD rather than Git Bash**, which rewrites
> arguments that look like Unix paths — `... attendance ls /data` becomes
> `ls C:/Program Files/Git/data` inside the container. Prefix with `MSYS_NO_PATHCONV=1` to
> stay in Git Bash.

Three more things worth knowing:

- **The container's clock is UTC** unless you pass `TIMEZONE`. Attendance times are
  recorded in the configured zone, so a register kept in one timezone and a container
  running in another disagree by however many hours separate them.
- **The camera is the browser's, not the container's.** Frames are captured by the page and
  posted to `/recognize`, so nothing needs a webcam passed into the container.
- **A stopped camera shows a full-screen red `NOT RECORDING` panel.** Nothing server-side
  can detect this state — no frames arrive, and silence is indistinguishable from an empty
  room — so the screen is the only alarm there is. If you see it, attendance is not being
  recorded and somebody needs to sign the station back in.

