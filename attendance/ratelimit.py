"""A small in-memory rate limiter for the login and signup forms.

Without one, /login accepts unlimited password guesses and /signup accepts unlimited
account creation -- which is both a way to brute-force a password and a way to bury the
admin approval page under thousands of junk requests.

Scope, stated plainly because it matters: the counters live in this process's memory.
They reset when the server restarts, and two worker processes each keep their own, so
running four workers effectively multiplies every limit by four. For one classroom-sized
deployment that is fine. For anything larger the replacement is Flask-Limiter backed by
Redis, which keeps the counts somewhere all the workers can see.
"""

import time
from collections import defaultdict, deque
from functools import wraps

from flask import current_app, make_response, render_template, request

# stop the store growing without bound if someone cycles through source addresses
MAX_TRACKED_CLIENTS = 10_000


def get_store():
    """The per-application attempt log: {(bucket, client): deque[timestamp]}.

    Held on the app rather than in a module-level global so that each application gets
    its own. That is what stops one test's failed logins counting against the next
    test's, and it costs nothing in production where there is a single app.
    """
    return current_app.extensions.setdefault("rate_limits", defaultdict(deque))


def client_key():
    # remote_addr, deliberately not X-Forwarded-For: that header is trivially spoofed
    # unless a trusted proxy is known to be overwriting it, and trusting it blindly would
    # let anyone bypass the limit by inventing an address per request. Behind a real
    # proxy, wrap the app in werkzeug's ProxyFix instead so remote_addr is correct.
    return request.remote_addr or "unknown"


def is_rate_limited(bucket, limit, per_seconds):
    """Record an attempt. Returns seconds to wait if over the limit, else None."""
    store = get_store()
    now = time.monotonic()  # monotonic, so a clock change cannot rewind the window
    hits = store[(bucket, client_key())]

    # drop the timestamps that have aged out, which is what makes this a sliding window
    # rather than a fixed one -- a fixed window lets someone use their whole allowance
    # at the end of one window and again at the start of the next
    cutoff = now - per_seconds
    while hits and hits[0] < cutoff:
        hits.popleft()

    if len(hits) >= limit:
        return int(per_seconds - (now - hits[0])) + 1

    hits.append(now)

    if len(store) > MAX_TRACKED_CLIENTS:
        prune(store, now)

    return None


def prune(store, now):
    """Forget clients whose attempts have all aged out."""
    for key in [k for k, hits in store.items() if not hits or hits[-1] < now - 3600]:
        del store[key]


def rate_limit(limit, per_seconds, template=None):
    """Limit POSTs to a route by client address.

    GETs are left alone: fetching the login form is harmless, and limiting it would lock
    someone out of the page that explains they are locked out.

    template: re-render this with an error rather than returning a bare 429, so a person
    who mistyped their password a few times sees the normal page and an explanation.
    """
    def decorator(view):
        bucket = view.__name__

        @wraps(view)
        def wrapper(*args, **kwargs):
            if request.method == "POST":
                retry_after = is_rate_limited(bucket, limit, per_seconds)
                if retry_after is not None:
                    minutes = max(1, round(retry_after / 60))
                    message = f"Too many attempts. Please try again in about {minutes} minute{'' if minutes == 1 else 's'}."
                    if template:
                        response = make_response(render_template(template, error=message), 429)
                    else:
                        response = make_response(message, 429)
                    # the standard way to tell a client how long to wait; some tools and
                    # libraries honour it automatically
                    response.headers["Retry-After"] = str(retry_after)
                    return response
            return view(*args, **kwargs)

        return wrapper

    return decorator
