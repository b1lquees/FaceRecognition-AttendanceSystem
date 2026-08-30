"""A small in-memory rate limiter, applied per route.

Four routes use it, guarding three different things. /login accepts unlimited password
guesses without it and /account/password is a second place to make them; /signup accepts
unlimited account creation, which buries the admin approval page under junk requests. And
/recognize guards something else entirely -- not a secret but the CPU, because it is the
only route that costs real work per request, so one client looping on it can occupy the
server and leave the camera by the door unable to check anybody in.

Scope, stated plainly because it matters: the counters live in this process's memory.
They reset when the server restarts, and two worker processes each keep their own, so
running four workers effectively multiplies every limit by four. For one classroom-sized
deployment that is fine. For anything larger the replacement is Flask-Limiter backed by
Redis, which keeps the counts somewhere all the workers can see.
"""

import time
from collections import OrderedDict, deque
from functools import wraps

from flask import current_app, jsonify, make_response, render_template, request

# The hard ceiling on how many (bucket, client) counters are kept at once.
#
# It is a real ceiling now. It used to be the point at which prune() was called, which is
# not the same thing at all: prune only drops counters that have gone quiet, so somebody
# cycling through source addresses -- the exact case this exists for -- kept every one of
# them alive and the store grew straight past it anyway. Past the number, the scan then
# ran on every single request and freed nothing, so the limiter became its own cost.
#
# Over the ceiling something live has to go. See enforce_cap().
MAX_TRACKED_CLIENTS = 10_000

# How long a counter is kept after its last attempt.
#
# This has to be at least as long as the longest per_seconds any route asks for, which is
# signup's 3600. Anything shorter and prune() starts discarding counters that are still
# in force -- a limit that quietly stops limiting, which is the worst way for one to fail.
MAX_IDLE_SECONDS = 3600


def get_store():
    """The per-application attempt log: {(bucket, client): deque[timestamp]}.

    Held on the app rather than in a module-level global so that each application gets
    its own. That is what stops one test's failed logins counting against the next
    test's, and it costs nothing in production where there is a single app.

    An OrderedDict rather than a plain dict because the ordering is load-bearing: entries
    are moved to the end as they are touched, so the front of it is the least recently
    used and enforce_cap() can evict from there without scanning.
    """
    return current_app.extensions.setdefault("rate_limits", OrderedDict())


def client_key():
    # remote_addr, deliberately not X-Forwarded-For read here: that header is trivially
    # spoofed unless a trusted proxy is known to be overwriting it, and trusting it
    # blindly would let anyone bypass the limit by inventing an address per request.
    #
    # Behind a proxy, remote_addr is corrected once at the edge of the application rather
    # than reinterpreted here -- create_app() wraps the app in werkzeug's ProxyFix when
    # TRUSTED_PROXY_HOPS says how many hops to believe. So this stays a plain read, and
    # there is exactly one place that decides what a client's address is.
    return request.remote_addr or "unknown"


def is_rate_limited(bucket, limit, per_seconds, key=None):
    """Record an attempt. Returns seconds to wait if over the limit, else None.

    key: what counts as "one client" for this bucket, as a callable returning a string.
    Defaults to the address. See rate_limit() for why a route would want anything else.
    """
    store = get_store()
    now = time.monotonic()  # monotonic, so a clock change cannot rewind the window
    counter = (bucket, (key or client_key)())

    hits = store.get(counter)
    if hits is None:
        hits = store[counter] = deque()

    # Touched, so it moves to the end -- and this happens BEFORE the limit check below,
    # not after. A client who is being refused is the most active one there is; if only
    # the allowed path counted as a touch, theirs would drift to the front of the queue
    # and be the first thing enforce_cap() evicted, which hands them a fresh allowance.
    # That is the limiter switching itself off for whoever is trying hardest.
    store.move_to_end(counter)

    # drop the timestamps that have aged out, which is what makes this a sliding window
    # rather than a fixed one -- a fixed window lets someone use their whole allowance
    # at the end of one window and again at the start of the next
    cutoff = now - per_seconds
    while hits and hits[0] < cutoff:
        hits.popleft()

    if len(hits) >= limit:
        return int(per_seconds - (now - hits[0])) + 1

    hits.append(now)

    # only reached on the allowed path, which is the only path that can add a key: a
    # brand new entry has an empty deque, so it can never be the one being refused
    enforce_cap(store, now)

    return None


def prune(store, now):
    """Forget clients whose attempts have all aged out."""
    for key in [
        k for k, hits in store.items() if not hits or hits[-1] < now - MAX_IDLE_SECONDS
    ]:
        del store[key]


def enforce_cap(store, now):
    """Hold the store at or under MAX_TRACKED_CLIENTS, whatever that takes.

    The quiet counters go first, because dropping those costs nothing -- nobody is being
    limited by a counter whose attempts have all aged out. If that is not enough, which is
    precisely the case this exists for, live counters go too: least recently touched
    first, which is the closest thing to "least likely to be mid-attack" available here.

    Evicting a live counter does give that client a fresh allowance, and somebody able to
    cycle ten thousand addresses can use that to flush a specific victim's counter out.
    That is inherent to keeping counts in bounded memory; the alternative is to keep every
    counter and run out of it, which is the failure this replaced. It is the same boundary
    the module docstring draws: past a certain size the answer is Flask-Limiter and Redis,
    where the counts do not live in one process's heap.
    """
    if len(store) <= MAX_TRACKED_CLIENTS:
        return

    prune(store, now)

    # popitem(last=False) takes from the front, which after move_to_end is the oldest
    while len(store) > MAX_TRACKED_CLIENTS:
        store.popitem(last=False)


def rate_limit(limit, per_seconds, template=None, as_json=False, key=None):
    """Limit POSTs to a route, by client address unless told otherwise.

    GETs are left alone: fetching the login form is harmless, and limiting it would lock
    someone out of the page that explains they are locked out.

    template: re-render this with an error rather than returning a bare 429, so a person
    who mistyped their password a few times sees the normal page and an explanation.

    as_json: answer with the same {"error": ...} shape the route uses when it succeeds.
    /recognize is called by javascript that parses the body before it looks at the status
    code, so a plain-text 429 surfaces to the user as "could not reach the server" --
    technically a refusal, but pointing at the wrong cause entirely.

    key: a callable deciding what counts as one client here, when the address does not.
    The address is the right answer for the three routes that guard a password or the
    approval queue -- those are attacked by somebody who is not signed in, so the session
    tells you nothing and the address is all there is. It is the wrong answer for a route
    that is only ever called by people who ARE signed in and who share an office: one
    address there is not one client, it is the building's gateway. Whatever this returns
    is the second half of the store key, so it must not be confusable with an address --
    prefix it. See checkin_client_key() in routes/recognition.py for the case this exists
    for.
    """
    def decorator(view):
        # Checked at decoration time, which is import time, so a limit that prune() would
        # sabotage cannot ship at all. The coupling is easy to miss otherwise: prune()
        # drops a counter once it has been idle for MAX_IDLE_SECONDS and has no idea which
        # window that counter belongs to, so a window longer than the threshold means
        # counters being discarded while they are still in force -- a limit that silently
        # stops limiting, which is the failure you least want to find in production.
        if per_seconds > MAX_IDLE_SECONDS:
            raise ValueError(
                f"per_seconds={per_seconds} exceeds MAX_IDLE_SECONDS={MAX_IDLE_SECONDS}, "
                "so prune() would discard this counter while it was still in force. "
                "Raise MAX_IDLE_SECONDS to at least this window."
            )

        bucket = view.__name__

        @wraps(view)
        def wrapper(*args, **kwargs):
            if request.method == "POST":
                retry_after = is_rate_limited(bucket, limit, per_seconds, key)
                if retry_after is not None:
                    minutes = max(1, round(retry_after / 60))
                    plural = "" if minutes == 1 else "s"
                    message = (
                        f"Too many attempts. Please try again in about "
                        f"{minutes} minute{plural}."
                    )
                    if template:
                        response = make_response(render_template(template, error=message), 429)
                    elif as_json:
                        response = make_response(jsonify({"error": message}), 429)
                    else:
                        response = make_response(message, 429)
                    # the standard way to tell a client how long to wait; some tools and
                    # libraries honour it automatically
                    response.headers["Retry-After"] = str(retry_after)
                    return response
            return view(*args, **kwargs)

        return wrapper

    return decorator
