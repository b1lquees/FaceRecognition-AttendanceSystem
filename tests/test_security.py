import re
import time
from collections import deque
from datetime import timedelta
from ipaddress import ip_network

import pytest

from attendance import create_app, ratelimit
from attendance.auth_db import create_user, verify_user
from attendance.config import Config, ProductionConfig, TestingConfig
from attendance.db import DEFAULT_DB

# --- rate limiting --------------------------------------------------------------

def post_login(client, csrf, password="wrong-password"):
    return client.post(
        "/login",
        data={"username": "viewer1", "password": password, "_csrf_token": csrf},
    )


def test_repeated_failed_logins_are_eventually_refused(client, csrf):
    create_user("viewer1", "the-real-password")

    # the limit is 10 per 5 minutes, so the first ten are allowed through to the normal
    # "invalid password" answer
    for _ in range(10):
        assert post_login(client, csrf).status_code == 401

    blocked = post_login(client, csrf)
    assert blocked.status_code == 429
    assert b"Too many attempts" in blocked.data


def test_the_limit_response_says_how_long_to_wait(client, csrf):
    create_user("viewer1", "the-real-password")
    for _ in range(11):
        response = post_login(client, csrf)

    assert response.headers["Retry-After"].isdigit()
    assert 0 < int(response.headers["Retry-After"]) <= 301


# being rate limited must not become a way to lock a legitimate user out permanently, so
# the correct password is still refused while blocked -- but only while blocked
def test_a_blocked_client_is_refused_even_with_the_right_password(client, csrf):
    create_user("viewer1", "the-real-password")
    for _ in range(11):
        post_login(client, csrf)

    response = post_login(client, csrf, password="the-real-password")

    assert response.status_code == 429


# fetching the form is harmless, and limiting it would hide the page that explains the
# limit from the person who hit it
def test_get_requests_to_login_are_never_limited(client, csrf):
    create_user("viewer1", "the-real-password")
    for _ in range(11):
        post_login(client, csrf)

    assert client.get("/login").status_code == 200


def test_signup_is_limited_separately_from_login(client, csrf):
    create_user("viewer1", "the-real-password")
    for _ in range(11):
        post_login(client, csrf)  # exhaust the login bucket

    # signup has its own bucket, so it is unaffected
    response = client.post(
        "/signup",
        data={
            "username": "newperson",
            "password": "password123",
            "confirm_password": "password123",
            "_csrf_token": csrf,
        },
    )

    assert response.status_code == 200


def test_signup_has_its_own_lower_limit(client, csrf):
    for i in range(5):
        client.post(
            "/signup",
            data={
                "username": f"person{i}",
                "password": "password123",
                "confirm_password": "password123",
                "_csrf_token": csrf,
            },
        )

    response = client.post(
        "/signup",
        data={
            "username": "onetoomany",
            "password": "password123",
            "confirm_password": "password123",
            "_csrf_token": csrf,
        },
    )

    assert response.status_code == 429


# the counters hang off the app, not a module global. if that regresses, one test's
# failed logins would start counting against the next test's and the suite would fail
# in confusing, order-dependent ways.
def test_each_application_gets_its_own_counters(client, csrf, app):
    create_user("viewer1", "the-real-password")
    for _ in range(11):
        post_login(client, csrf)

    fresh_client = app.test_client()
    with fresh_client.session_transaction() as session:
        session["_csrf_token"] = csrf

    # same app here, so still blocked -- this is the control for the assertion below
    assert post_login(fresh_client, csrf).status_code == 429
    assert "rate_limits" in app.extensions


# --- user enumeration by timing -------------------------------------------------

# a missing username used to return immediately while a real one paid for a full scrypt
# comparison. that difference is measurable over a network and tells an attacker which
# usernames exist. both paths now do the same work.
def test_unknown_and_known_usernames_take_a_similar_time(temp_db):
    create_user("realuser", "the-real-password")

    def measure(username):
        samples = []
        for _ in range(3):
            start = time.perf_counter()
            verify_user(username, "some-wrong-password")
            samples.append(time.perf_counter() - start)
        return min(samples)  # min is the least noisy estimate of the true cost

    known = measure("realuser")
    unknown = measure("no-such-user")

    # generous bound: this is asserting the dummy hash is being computed at all, not
    # measuring a precise ratio, because CI timing is noisy
    assert unknown > known / 3, (
        f"unknown-user path was much faster ({unknown:.4f}s vs {known:.4f}s), "
        "so the dummy hash comparison is probably missing"
    )


# --- session cookie hardening ---------------------------------------------------

def test_session_cookie_is_not_readable_by_javascript():
    assert Config.SESSION_COOKIE_HTTPONLY is True


def test_session_cookie_is_not_sent_on_cross_site_posts():
    assert Config.SESSION_COOKIE_SAMESITE == "Lax"


# without this, one plain-http request leaks the session cookie in cleartext, and the
# cookie is the whole login
def test_production_requires_https_for_the_session_cookie():
    assert ProductionConfig.SESSION_COOKIE_SECURE is True


# development and tests run over plain http, so the flag has to be off there or nothing
# would stay logged in
@pytest.mark.parametrize("config", [Config, TestingConfig])
def test_secure_cookie_is_off_outside_production(config):
    assert config.SESSION_COOKIE_SECURE is False


def test_the_app_actually_applies_the_cookie_settings(app):
    assert app.config["SESSION_COOKIE_HTTPONLY"] is True
    assert app.config["SESSION_COOKIE_SAMESITE"] == "Lax"


# --- the session is rotated on login ----------------------------------------------
#
# A session that survives a privilege change carries whatever was in it into the
# authenticated one. Nobody can forge a signed cookie without the secret key, but anyone
# able to plant one -- a sibling subdomain, or any XSS on the origin -- would otherwise
# know a CSRF token that stays valid after the victim signs in.

def test_logging_in_starts_a_fresh_session(client, csrf):
    create_user("viewer1", "the-real-password")
    with client.session_transaction() as session:
        session["planted"] = "from before the login"

    post_login(client, csrf, password="the-real-password")

    with client.session_transaction() as session:
        assert "planted" not in session
        assert session["username"] == "viewer1"   # and the login itself still worked


def test_the_csrf_token_does_not_survive_the_login(client, csrf):
    create_user("viewer1", "the-real-password")

    post_login(client, csrf, password="the-real-password")

    with client.session_transaction() as session:
        # gone entirely: the next page rendered will mint a new one
        assert session.get("_csrf_token") != csrf


# a failed login is not a privilege change, so it must not throw the session away -- doing
# so would log out somebody who mistyped their password on a second tab
def test_a_failed_login_leaves_the_session_alone(client, csrf):
    create_user("viewer1", "the-real-password")

    post_login(client, csrf)  # wrong password

    with client.session_transaction() as session:
        assert session.get("_csrf_token") == csrf


# --- how long a session lasts -------------------------------------------------------
#
# This was always bounded -- Flask defaults it to 31 days and enforces it server-side when
# it unseals the cookie -- so the value was being chosen by a framework default nobody had
# read. Pinning it is mostly about the choice being visible.

def test_a_session_lasts_a_week():
    assert Config.PERMANENT_SESSION_LIFETIME == timedelta(days=7)


# Days, not hours, and the reason is availability rather than laziness. Sessions here are
# not permanent, so this is an absolute ceiling from login that traffic does not refresh --
# a short one expires the door camera mid-use, and a camera that has silently stopped
# recording is the worst failure this system has.
def test_the_ceiling_is_long_enough_not_to_expire_a_camera_mid_day():
    assert Config.PERMANENT_SESSION_LIFETIME >= timedelta(days=1)


# the mechanism itself, not just the number: Flask passes this as max_age when it unseals
# the cookie, so an expired session is refused by the server rather than trusted to a
# cookie attribute the client controls
def test_an_expired_session_is_refused_server_side(temp_db, csrf):
    class Brief(TestingConfig):
        PERMANENT_SESSION_LIFETIME = timedelta(seconds=1)

    client = seeded_client(Brief, csrf)
    create_user("viewer1", "the-real-password")
    post_login(client, csrf, password="the-real-password")
    assert client.get("/attendance/today").status_code == 200

    time.sleep(2)

    # bounced to the login page, without the client having deleted anything
    assert client.get("/attendance/today").status_code == 302


# --- response headers -------------------------------------------------------------

@pytest.mark.parametrize(
    "header, value",
    [
        ("X-Content-Type-Options", "nosniff"),
        ("X-Frame-Options", "DENY"),
        ("Referrer-Policy", "no-referrer"),
    ],
)
def test_security_headers_are_set(client, header, value):
    assert client.get("/login").headers[header] == value


# an injected <base> tag repoints every relative URL on the page, form posts included
def test_the_content_security_policy_pins_the_base_and_refuses_framing(client):
    policy = client.get("/login").headers["Content-Security-Policy"]

    assert "frame-ancestors 'none'" in policy
    assert "base-uri 'self'" in policy
    assert "form-action 'self'" in policy


# --- the script-src nonce -----------------------------------------------------------
#
# This is the directive that actually defends against XSS, and it is also the one that
# fails silently: get it wrong and the camera page stops working with no error anywhere the
# server can see. Hence rather more tests than the headers above.

def nonce_from(response):
    return re.search(r"nonce-([^']+)", response.headers["Content-Security-Policy"]).group(1)


def test_the_policy_allows_scripts_only_by_nonce(client):
    policy = client.get("/login").headers["Content-Security-Policy"]

    assert "script-src 'nonce-" in policy
    # the two escape hatches that would render the whole directive decorative
    assert "unsafe-inline" not in policy.split("script-src")[1].split(";")[0]
    assert "unsafe-eval" not in policy


# A nonce that repeats is worth nothing: anyone who has seen one page's HTML then knows the
# value that authorises script on the next one.
def test_every_response_gets_a_different_nonce(client):
    nonces = {nonce_from(client.get("/login")) for _ in range(5)}

    assert len(nonces) == 5


# and the page has to actually carry it, or every inline script on the site is dead
@pytest.mark.parametrize("path", ["/login", "/signup"])
def test_the_page_carries_the_same_nonce_the_header_names(client, path):
    response = client.get(path)

    assert f'nonce="{nonce_from(response)}"' in response.get_data(as_text=True)


def test_the_camera_page_scripts_carry_the_nonce(client, login):
    login()
    response = client.get("/camera")
    nonce = nonce_from(response)
    body = response.get_data(as_text=True)

    # base.html contributes two and camera.html two; a bare <script> among them would be
    # blocked by the policy and nothing server-side would ever notice
    assert body.count("<script") == body.count(f'<script nonce="{nonce}"')


# 'unsafe-inline' has to stay in style-src, because a nonce cannot authorise a style
# ATTRIBUTE and the templates still use a few. Asserted so the gap is deliberate and
# visible rather than something nobody remembered was there.
def test_inline_styles_are_still_allowed_and_that_is_known(client):
    policy = client.get("/login").headers["Content-Security-Policy"]
    style = policy.split("style-src")[1].split(";")[0]

    assert "unsafe-inline" in style


# every response, not just the pages somebody remembered to decorate
def test_the_headers_are_on_error_responses_too(client):
    assert client.get("/no-such-page").headers["X-Content-Type-Options"] == "nosniff"


# --- database path --------------------------------------------------------------

# sqlite creates any database it cannot find. when the default was the bare filename
# "attendance.db", running anything from another directory silently opened a new empty
# one, which looks exactly like every record having been lost.
def test_the_default_database_path_is_absolute():
    from pathlib import Path

    assert Path(DEFAULT_DB).is_absolute()
    assert Path(DEFAULT_DB).name == "attendance.db"


# --- the expensive endpoint ---------------------------------------------------------
#
# /recognize is the only route that costs real CPU: detection plus encoding is around
# 1.2s per frame, in the request thread, against two gunicorn workers. Every other limit
# in this file guards a password or the approval queue; this one guards the server's
# ability to answer anybody at all. A signed-in account looping on it was, until this
# limit existed, enough to stop the camera by the door working.

def post_frame(client, csrf):
    """A frame that fails validation early -- the limiter runs before the view either way."""
    return client.post(
        "/recognize",
        json={"image": "not-an-image"},
        headers={"X-CSRF-Token": csrf},
    )


def test_recognize_is_limited(client, login, csrf):
    login()

    for _ in range(60):
        assert post_frame(client, csrf).status_code == 400   # rejected on content, not rate

    assert post_frame(client, csrf).status_code == 429


# the camera posts a frame every 1.5 seconds, which is 40 a minute. A limit that a normal
# session trips is worse than no limit, because the first person to hit it turns it off.
def test_a_normal_camera_session_stays_well_inside_the_limit(client, login, csrf):
    login()

    for _ in range(40):
        assert post_frame(client, csrf).status_code != 429


# the page parses the body before it looks at the status, so a plain-text refusal reaches
# the user as "could not reach the server" -- a true statement about the wrong thing
def test_the_refusal_is_json_like_every_other_answer_from_this_route(client, login, csrf):
    login()
    for _ in range(61):
        post_frame(client, csrf)

    response = post_frame(client, csrf)

    assert response.status_code == 429
    assert response.is_json
    assert "error" in response.get_json()
    assert response.headers["Retry-After"]


# --- who "one client" is, which is not the same question in the two modes -------------
#
# The limit is 60 a minute against a camera that posts 40, so it is sized for exactly one
# camera. In kiosk mode one address IS one camera and counting by address is right. In
# personal mode it is not: everybody checks in from their own browser, and in an office
# those browsers leave through one gateway, so counting by address bills a whole building
# to a single 60-a-minute allowance. The second person to check in that morning gets a
# 429, having done nothing wrong and with the server barely working.
#
# Both tests share one client on one address deliberately -- that is the whole scenario.

def test_personal_mode_counts_each_account_separately(client, login, csrf, app):
    app.config["KIOSK_MODE"] = False

    login(username="alice", password="alice-password")
    for _ in range(61):
        post_frame(client, csrf)
    assert post_frame(client, csrf).status_code == 429   # alice has spent hers

    # same client, same address, different account -- and a check-in of her own to make
    login(username="bob", password="bob-password")
    assert post_frame(client, csrf).status_code != 429


def test_kiosk_mode_still_counts_by_address(client, login, csrf, app):
    app.config["KIOSK_MODE"] = True

    login(username="station", password="station-password")
    for _ in range(61):
        post_frame(client, csrf)
    assert post_frame(client, csrf).status_code == 429

    # signing the station in as somebody else does not buy a second allowance: the thing
    # being limited is the machine at the door, and it is the same machine
    login(username="relief", password="relief-password")
    assert post_frame(client, csrf).status_code == 429


# --- where a check-in may come from ---------------------------------------------------
#
# Recognition answers "who is in front of this camera" and cannot answer "where is this
# camera". In kiosk mode that gap is closed by somebody having screwed the camera to a
# wall. In personal mode nothing closes it: the account holder can check themselves in
# from their kitchen with their own real face, and neither a stricter TOLERANCE nor the
# liveness gate can tell, because it IS them and they ARE live.
#
# CHECKIN_NETWORKS is the constraint from outside recognition. It is not proof of presence
# -- a VPN passes -- but it moves the attack from "anywhere with a browser" to "on the
# premises, or deliberately tunnelling in".


class OnlyFromTheOffice(TestingConfig):
    CHECKIN_NETWORKS = (ip_network("192.168.1.0/24"),)


def frame_from(client, csrf, address):
    return client.post(
        "/recognize",
        json={"image": "not-an-image"},
        headers={"X-CSRF-Token": csrf, "X-Forwarded-For": address},
    )


def signed_in_client(config, csrf, temp_db):
    """A client on the given config, signed in, with the CSRF token seeded."""
    client = create_app(config).test_client()
    create_user("viewer1", "the-real-password")
    with client.session_transaction() as session:
        session["_csrf_token"] = csrf
    client.post(
        "/login",
        data={"username": "viewer1", "password": "the-real-password", "_csrf_token": csrf},
    )
    with client.session_transaction() as session:
        session["_csrf_token"] = csrf
    return client


class OfficeBehindOneProxy(OnlyFromTheOffice):
    TRUSTED_PROXY_HOPS = 1


def test_an_address_on_the_permitted_network_is_let_through(temp_db, csrf):
    client = signed_in_client(OfficeBehindOneProxy, csrf, temp_db)

    # 400 is the frame failing validation, which means it reached the view at all
    assert frame_from(client, csrf, "192.168.1.40").status_code == 400


def test_an_address_outside_it_is_refused(temp_db, csrf):
    client = signed_in_client(OfficeBehindOneProxy, csrf, temp_db)

    response = frame_from(client, csrf, "203.0.113.9")

    assert response.status_code == 403
    assert response.get_json()["offsite"] is True


# the page has to tell this apart from the session expiring, which is the other thing that
# answers 403 and stops the loop. only one of them can be explained accurately.
def test_the_refusal_is_json_the_camera_page_can_act_on(temp_db, csrf):
    client = signed_in_client(OfficeBehindOneProxy, csrf, temp_db)

    response = frame_from(client, csrf, "203.0.113.9")

    assert response.is_json
    assert "error" in response.get_json()


def test_the_refusal_is_audited(temp_db, csrf, caplog):
    client = signed_in_client(OfficeBehindOneProxy, csrf, temp_db)

    with caplog.at_level("INFO"):
        frame_from(client, csrf, "203.0.113.9")

    assert "checkin.offsite" in caplog.text
    assert "203.0.113.9" in caplog.text   # audit() records where, which is the point


# An empty setting is every deployment that exists today, and it must stay a no-op.
def test_an_empty_setting_restricts_nothing(temp_db, csrf):
    client = signed_in_client(TestingConfig, csrf, temp_db)

    assert frame_from(client, csrf, "203.0.113.9").status_code == 400


# The gate fails closed on anything it cannot place, because "I could not tell" resolving
# to yes is how a gate becomes decoration. A site answering on both families has to list
# both, and this is the test that says so.
def test_an_address_of_an_unlisted_family_is_refused(temp_db, csrf):
    client = signed_in_client(OfficeBehindOneProxy, csrf, temp_db)

    assert frame_from(client, csrf, "2001:db8::1").status_code == 403


def test_an_unparseable_address_is_refused(temp_db, csrf):
    client = signed_in_client(OfficeBehindOneProxy, csrf, temp_db)

    assert frame_from(client, csrf, "not-an-address").status_code == 403


# Without TRUSTED_PROXY_HOPS the forwarded header is ignored, so behind a proxy every
# request carries the proxy's own address -- and the proxy is usually ON the network
# somebody just listed. The gate then admits the entire internet while looking configured,
# which is the failure worth having a test for rather than a sentence.
def test_without_a_trusted_proxy_the_forwarded_address_does_not_open_the_gate(temp_db, csrf):
    client = signed_in_client(OnlyFromTheOffice, csrf, temp_db)

    # the test client's own address is 127.0.0.1, which is not on the permitted network,
    # and claiming to be on it in a header changes nothing
    assert frame_from(client, csrf, "192.168.1.40").status_code == 403


# A bucket per view, so spending one route's allowance cannot lock anyone out of another.
#
# This has to POST to prove anything. The decorator skips non-POST requests entirely, so
# asserting that GET /login still works would pass just as happily if every route in the
# application shared a single bucket -- it tests the method check, not the bucketing.
def test_recognize_has_its_own_bucket(client, login, csrf):
    login()
    for _ in range(61):
        post_frame(client, csrf)      # exhaust the recognize bucket

    # a POST to a different view, which shares nothing with it but the store
    assert post_login(client, csrf).status_code == 401


# Only this direction is worth asserting, and the asymmetry is the reason. Recognize's 60
# is well above login's 10, so a shared bucket would visibly lock login out -- which is
# what the test above catches. The reverse cannot be written honestly: spending login's
# allowance stops at 10 attempts, never reaching recognize's 60, so a test that exhausted
# login and then posted a frame would pass whether the buckets were separate or merged.


# --- who the limiter thinks it is talking to ----------------------------------------
#
# Production sets SESSION_COOKIE_SECURE, so it needs TLS terminated in front of it, so it
# is always behind a proxy -- and behind a proxy remote_addr is the proxy's own address,
# identically for every client. Uncorrected, that turns every limit in ratelimit.py into
# one bucket shared by the whole internet, and every line of the audit trail into the same
# address. With /recognize limited too it stops being merely weak and becomes an outage:
# one camera posts 40 frames a minute, so two of them between them exceed 60 and check-in
# stops working for everybody.
#
# These two tests are the before and the after.

class TrustsOneProxy(TestingConfig):
    TRUSTED_PROXY_HOPS = 1


def seeded_client(config, csrf):
    """A client on an app built with the given config, with the CSRF token already set."""
    client = create_app(config).test_client()
    with client.session_transaction() as session:
        session["_csrf_token"] = csrf
    return client


def login_from(client, csrf, address):
    return client.post(
        "/login",
        data={"username": "viewer1", "password": "wrong-password", "_csrf_token": csrf},
        headers={"X-Forwarded-For": address},
    )


def test_without_a_trusted_proxy_the_forwarded_address_is_ignored(temp_db, csrf):
    client = seeded_client(TestingConfig, csrf)
    create_user("viewer1", "the-real-password")

    for _ in range(11):
        login_from(client, csrf, "1.1.1.1")

    # a different client entirely, and it is already locked out -- which is correct
    # behaviour for an app that has NOT been told there is a proxy in front of it, because
    # believing that header without one lets anybody invent an address per request
    assert login_from(client, csrf, "2.2.2.2").status_code == 429


def test_behind_a_trusted_proxy_clients_are_counted_separately(temp_db, csrf):
    client = seeded_client(TrustsOneProxy, csrf)
    create_user("viewer1", "the-real-password")

    for _ in range(11):
        login_from(client, csrf, "1.1.1.1")

    # somebody else's browser, arriving through the same proxy, is unaffected
    assert login_from(client, csrf, "2.2.2.2").status_code == 401


def test_the_forwarded_address_reaches_the_limiter_intact(temp_db, csrf):
    client = seeded_client(TrustsOneProxy, csrf)
    create_user("viewer1", "the-real-password")

    for _ in range(11):
        login_from(client, csrf, "1.1.1.1")

    # and the client who did spend the allowance is still the one refused
    assert login_from(client, csrf, "1.1.1.1").status_code == 429


# --- the ceiling on how many counters are kept ---------------------------------------
#
# MAX_TRACKED_CLIENTS used to be the number at which prune() got called, which is not the
# same thing as a ceiling. prune() only drops counters that have gone quiet, so a client
# cycling through source addresses -- the one case the ceiling exists for -- kept every
# one of theirs alive and the store grew straight past it. Past that number the scan then
# ran on every single request and freed nothing, so the limiter became its own cost.

def fill_store(store, count, now, bucket="login"):
    for i in range(count):
        store[(bucket, f"10.0.{i // 256}.{i % 256}")] = deque([now])


def test_the_store_is_held_at_the_ceiling(app):
    with app.test_request_context("/"):
        store = ratelimit.get_store()
        now = time.monotonic()
        fill_store(store, ratelimit.MAX_TRACKED_CLIENTS + 500, now)

        ratelimit.enforce_cap(store, now)

        assert len(store) == ratelimit.MAX_TRACKED_CLIENTS


# the same thing through the real path rather than by calling enforce_cap() directly:
# every one of these is a brand new address arriving at a store already full
def test_cycling_addresses_cannot_grow_the_store_without_bound(app):
    with app.test_request_context("/"):
        fill_store(ratelimit.get_store(), ratelimit.MAX_TRACKED_CLIENTS, time.monotonic())

    for i in range(20):
        with app.test_request_context("/", environ_base={"REMOTE_ADDR": f"203.0.113.{i}"}):
            ratelimit.is_rate_limited("login", limit=10, per_seconds=300)

    with app.test_request_context("/"):
        assert len(ratelimit.get_store()) <= ratelimit.MAX_TRACKED_CLIENTS


def test_counters_that_have_gone_quiet_are_dropped_first(app):
    with app.test_request_context("/"):
        store = ratelimit.get_store()
        now = time.monotonic()
        store[("login", "quiet")] = deque([now - ratelimit.MAX_IDLE_SECONDS - 1])
        store[("login", "active")] = deque([now])

        ratelimit.prune(store, now)

        assert list(store) == [("login", "active")]


# The eviction order has to count a refusal as activity. If only allowed attempts moved a
# counter to the back of the queue, the client hammering the login form would drift to the
# front and be the first thing evicted -- handing them a fresh allowance. The limiter would
# switch itself off for whoever was trying hardest, which is the opposite of the job.
def test_a_client_being_refused_still_counts_as_active(app):
    attacker = {"REMOTE_ADDR": "198.51.100.7"}

    for _ in range(10):
        with app.test_request_context("/", environ_base=attacker):
            assert ratelimit.is_rate_limited("login", 10, 300) is None

    with app.test_request_context("/", environ_base=attacker):
        assert ratelimit.is_rate_limited("login", 10, 300) is not None  # refused
        store = ratelimit.get_store()

        # at the back of the queue (evicted last), not the front
        assert next(reversed(store)) == ("login", "198.51.100.7")


# prune() drops a counter once it has been idle for MAX_IDLE_SECONDS, knowing nothing
# about which window it belongs to. A route asking for a longer window than that would
# have its counters thrown away while they were still in force, so the mismatch is refused
# at decoration time -- which is import time, so it cannot reach a running server.
def test_a_window_longer_than_the_idle_threshold_is_refused():
    with pytest.raises(ValueError, match="MAX_IDLE_SECONDS"):
        ratelimit.rate_limit(limit=5, per_seconds=ratelimit.MAX_IDLE_SECONDS + 1)(
            lambda: None
        )


def test_the_windows_actually_in_use_are_all_within_the_threshold():
    # signup's hour is the longest, and it is exactly the threshold
    assert ratelimit.MAX_IDLE_SECONDS >= 3600

