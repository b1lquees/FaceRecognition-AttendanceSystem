# Security

How this system decides who may do what, what a recorded check-in does and does not prove,
and where the boundaries are. The [security summary in the README](../README.md#security)
is the short version; this is the reasoning underneath it.

- [Threat model](#threat-model)
- [Accounts and approval](#accounts-and-approval)
- [Kiosk mode vs. personal check-in](#kiosk-mode-vs-personal-check-in)
- [Where a check-in may come from](#where-a-check-in-may-come-from)
- [Sessions, CSRF and rate limiting](#sessions-csrf-and-rate-limiting)
- [Audit trail](#audit-trail)

## Threat model

Every row is a control that exists in the code, and the honest statement of what it still
leaves open. The right-hand column is the useful one: a mitigation described without its
remaining limitation is how a gap gets deployed as a feature.

| Threat | Mitigation | Remaining limitation |
| --- | --- | --- |
| Guessed or brute-forced password | Hashed passwords; 10 login attempts per 5 minutes, 10 password changes per 5 minutes | Counters live in process memory, so they reset on restart and each worker keeps its own |
| Self-granted privilege at signup | Signup always creates a **pending** `viewer`; role is not a form field; promotion is a shell command | An approved admin account can do everything the site can do, so approval is the control |
| Cross-site request forgery | Session-held token on every `POST`; `/recognize` sends it as an `X-CSRF-Token` header | — |
| Session fixation | The session is cleared at login, so nothing from before the sign-in carries into it | — |
| Forged session cookie | Cookies signed with `FLASK_SECRET_KEY`; production refuses to start without one | Signed cookies cannot be revoked — a leaked key means rotating it, which signs out everyone including the camera station |
| Injected script | CSP with a per-response `script-src 'nonce-…'` | `style-src` still allows inline styles, so injected CSS is not covered |
| Photograph or screen held to the camera | Liveness gate runs **before** recognition, so a spoof is never identified | Ships disabled, and on the camera measured here it has to stay off — see [liveness calibration](liveness-calibration.md) |
| One person marked present by another | `KIOSK_MODE=0` refuses any face that is not the signed-in account's linked person | Kiosk mode never consults the session — that is what it is for |
| Check-in from somewhere other than the camera | `CHECKIN_NETWORKS` refuses frames from outside the listed CIDRs, before decoding anything | A VPN back into those networks passes: a policy control, not proof of presence |
| Spoofed client address | `TRUSTED_PROXY_HOPS` trusts only as many `X-Forwarded-For` hops as you actually run | Set higher than the real number and any client can claim any address |
| Recognition CPU exhausted by frames | 60 frames a minute on `/recognize`, per address in kiosk mode and per account in personal mode | Same in-memory counters, capped at 10,000 and LRU-evicted |
| Quiet misuse of an admin account | Audit trail records who did it and from where | The log is only as durable as wherever `LOG_FILE` points |
| Loss of the register and the enrolled faces | Documented snapshot and restore — see [backup and restore](backup-restore.md) | The backup is biometric data and inherits every obligation the live system has |
| Unattended admin session at the door | The station signs in as a `viewer`, which needs no admin rights | Nothing enforces it; it is a deployment choice somebody has to make correctly |

## Accounts and approval

Anyone can request an account at `/signup`, but it is created **pending** and cannot sign
in until an administrator approves it at `/admin/users`. Signup always creates a `viewer`
— the role is not a form field, because if it were, anyone reaching the page could make
themselves an administrator.

Promotion is deliberately a command-line action, so it always requires access to the
server:

```bash
python scripts/create_user.py alice --role admin
```

Accounts created that way are approved immediately: someone with shell access does not
need to ask themselves for permission.

Removing a person at `/admin/enrol` deletes their photos and face data but **keeps their
attendance history**. Un-enrolling stops the system recognising them from now on; it does
not mean they were never there.

## Kiosk mode vs. personal check-in

Two quite different models, chosen with `KIOSK_MODE`:

**Kiosk (`KIOSK_MODE=1`, the default)** — one camera by a door. Anyone enrolled who is
recognised is marked present, whoever happens to be signed in at the station. The account
is the operator running the terminal, not the person being recorded.

**Personal (`KIOSK_MODE=0`)** — each person signs into their own account and checks
themselves in. A recognised face that does not belong to the signed-in account is refused.
This is what makes *"only the registered person can check in"* true: in kiosk mode, being
signed in as anyone lets you mark anyone else present, because the recording path never
consults the session.

Personal mode requires each account to be **linked to an enrolled person**, done by an
administrator at `/admin/users`. Linking is admin-only by design — if people chose their
own identity at signup, anyone could claim to be anyone, which is the exact impersonation
the mode exists to prevent. An unlinked account cannot check in at all.

A refused check-in deliberately does **not** report who was actually recognised. Doing so
would tell the signed-in user who was standing in front of the camera, leaking other
people's presence to anyone able to point a webcam at them.

> Neither mode defends against a **photograph** of the right person — see
> [Anti-spoofing](../README.md#anti-spoofing).

> Personal mode is stronger on *who* and weaker on *where*: the account holder can check
> themselves in from anywhere they can reach the site, and neither recognition nor liveness
> can tell. See [Where a check-in may come from](#where-a-check-in-may-come-from).

## Where a check-in may come from

Recognition answers *who is in front of this camera* and has no way to answer *where is
this camera*. Kiosk mode does not need it to: somebody screwed the camera to a wall, so
the location is a fact about the hardware. Personal mode has no such fact, and this is the
consequence — an enrolled person can sit at home, point their own laptop at their own face
and be marked present, with a correct password, a live face and a distance well inside the
tolerance. Every control in the system returns the right answer.

Nothing added to the recognition path fixes that. A stricter `TOLERANCE` does not, because
it is the right person; the liveness gate does not, because it is a live face. The check
that is missing is a different kind of check, so it comes from outside:

```bash
CHECKIN_NETWORKS=10.0.0.0/8,192.168.1.0/24
```

`/recognize` then refuses any frame from an address outside that list, before decoding
anything, with a `403` and an audited `checkin.offsite` line. Unset — the default — means
no restriction, so existing deployments are unaffected. It applies in both modes: a kiosk
that wants its door camera pinned to the LAN uses the same setting.

Be clear about its strength. Anyone on a VPN into those networks passes, so this is a
policy control of the same class as a door badge rather than proof of presence. What it
buys is moving the attack from *anywhere with a browser* to *on the premises, or
deliberately tunnelling in* — a real change of category, not a solved problem.

Three failure modes to watch for:

- **`TRUSTED_PROXY_HOPS` has to be right**, or the gate is meaningless. Behind an
  uncorrected proxy every request appears to come from the proxy, and the proxy is usually
  *on* the network you just listed — so the gate admits the entire internet while looking
  configured.
- **It fails closed on anything it cannot place**, including an address family nobody
  listed. A site answering on both IPv4 and IPv6 must list both, or the v6 clients are all
  refused.
- **A typo stops the application starting.** An unparseable entry is a hard error rather
  than a fallback, because falling back would silently remove the restriction — the one
  direction this setting must never fail in.

> Even with it, a phone on the office wifi plus the right face is a valid check-in from the
> car park. A browser webcam can prove who is in front of it; proving where it is standing
> needs hardware you control, which is what kiosk mode is.

## Sessions, CSRF and rate limiting

The secret key signs session cookies, and anyone who knows it can forge one claiming
`role=admin`. In development a key is generated and cached in `.flask_secret_dev`
(gitignored). In production `FLASK_SECRET_KEY` is required, and its absence stops the
application starting rather than falling back to something guessable.

Responses carry a Content-Security-Policy. The part that does the work is
`script-src 'nonce-…'`: every inline `<script>` is tagged with a value generated fresh for
that response, so the page's own scripts run and injected ones do not — an injection cannot
carry a nonce it has never seen. `style-src` still allows inline styles, because a nonce
cannot authorise a style *attribute* and a few remain in the templates; injected CSS is a
much narrower problem than injected script, but it is a real gap rather than an oversight.

Logging in clears the session first, so nothing from before the sign-in — the CSRF token
included — carries into the authenticated one.

Sessions last **seven days**, counted from login. This was always bounded, at Flask's
default of 31 days, and enforced server-side; seven is simply a value somebody chose. Be
clear about what it is not: with signed cookies there is no server-side session to
invalidate, so nothing can revoke a leaked cookie early short of rotating
`FLASK_SECRET_KEY`, which signs out everyone including the camera station. The ceiling is
absolute rather than idle — traffic does not extend it — which is why it is measured in
days: a shorter one would expire the door camera mid-use, and a camera that has stopped
recording is the failure this system can least afford.

> **Sign the camera station in as a `viewer`, not an `admin`.** In kiosk mode the account
> is the operator, not the person being recorded, so it needs no admin rights — and an
> unattended admin session sitting at a door is worth more to an attacker than any session
> length protects against. This is the cheapest security decision available here.

Every `POST` is CSRF-protected by a token held in the session
([`attendance/security.py`](../attendance/security.py)). Forms carry it in a hidden field;
`/recognize` posts JSON, so it sends the same value as an `X-CSRF-Token` header.

Four routes are rate limited ([`attendance/ratelimit.py`](../attendance/ratelimit.py)): 10
login attempts per 5 minutes, 10 password changes per 5 minutes, 5 signups per hour, and
60 frames a minute on `/recognize`. The first three guard a password or the approval
queue; the last one guards the CPU, since recognising a frame is the only genuinely
expensive thing the server does.

The first three count per client address, which is the only unit available — they are
attacked by somebody who is not signed in, so the session says nothing. `/recognize`
counts per address in kiosk mode and **per account in personal mode**, because the limit
is sized for one camera and in personal mode one address is not one camera: twenty people
checking in from their own browsers leave an office through one gateway, and counting them
together would 429 everyone after the first.

The counters live in process memory, so they reset on restart and each worker keeps its
own — adequate for a single classroom-sized deployment, but anything larger wants
Flask-Limiter backed by Redis so the count is shared. The store is capped at 10,000
counters and evicts the least recently used beyond that, so cycling through source
addresses exhausts memory in neither direction.

**Set `TRUSTED_PROXY_HOPS` if there is a proxy in front**, or all of these collapse into
one bucket shared by every client — see [Configuration](../README.md#configuration).

## Audit trail

Security-relevant actions are written to a log
([`attendance/audit.py`](../attendance/audit.py)): logins and failures, signups, approvals,
rejections, account linking, enrolment, removal, refused spoof attempts, and check-ins
refused for coming from off site — each with who did it and from where. Passwords, tokens
and face encodings are never logged.

