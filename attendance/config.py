"""Settings for each environment the app runs in.

Previously every setting was assigned at the top of app.py, which meant there was
exactly one configuration and it was decided the moment the module was imported.
Splitting them into classes lets the tests build an app that never touches the real
database or the real secret key, without setting environment variables first.
"""

import os
import secrets
from pathlib import Path

# config.py lives in attendance/, so the project root is one level up
PROJECT_ROOT = Path(__file__).resolve().parent.parent

DEV_KEY_FILE = PROJECT_ROOT / ".flask_secret_dev"

FALSY = ("0", "false", "no", "off")


def env_flag(name, default):
    """Read a boolean setting from the environment.

    An unset variable and an empty one both mean "use the default". That second case is
    not hypothetical: `setx NAME ""` is how people clear a variable on Windows, and it
    leaves an empty string behind rather than removing it. Treating empty as truthy --
    which a bare `value not in FALSY` does -- meant the ordinary way to turn a setting
    off would turn it on instead.
    """
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() not in FALSY


def env_float(name, default):
    """Read a numeric setting, failing loudly rather than silently falling back.

    A threshold that has been typo'd should stop the app, not quietly revert to a default
    that behaves differently from what someone believed they configured.
    """
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError:
        raise RuntimeError(
            f"{name} must be a number, got {raw!r}"
        ) from None


def generate_dev_secret_key():
    """Return a development key, generating and caching one on first use.

    Caching matters because the debug reloader restarts the process every time you
    save a file. A fresh key each restart would invalidate the session cookie and
    log you out constantly.
    """
    if DEV_KEY_FILE.exists():
        return DEV_KEY_FILE.read_text().strip()

    key = secrets.token_hex(32)  # cryptographically secure random bytes as hex
    DEV_KEY_FILE.write_text(key)
    print("No FLASK_SECRET_KEY set - generated a development key in .flask_secret_dev")
    return key


class Config:
    """Settings shared by every environment."""

    DEBUG = False
    TESTING = False

    # a webcam frame is roughly 50-100 KB as base64. anything far above that is either
    # a bug or an attempt to exhaust the server's memory, so Flask rejects the request
    # with 413 before reading the body.
    MAX_CONTENT_LENGTH = 8 * 1024 * 1024  # 8 MB

    # --- session cookie hardening ---
    # HttpOnly keeps JavaScript from reading the cookie, so an injected script cannot
    # steal a session. Flask defaults this to True already; it is stated explicitly
    # because a security-relevant default is worth being visible rather than assumed.
    SESSION_COOKIE_HTTPONLY = True

    # Lax stops the cookie being sent on cross-site POSTs, which is a second line of
    # defence behind the CSRF tokens in security.py. "Strict" would also drop it when
    # following an ordinary link from another site, which would log people out for no
    # security gain here.
    SESSION_COOKIE_SAMESITE = "Lax"

    # False by default so the cookie still works over plain http in development.
    # ProductionConfig turns it on -- see the note there.
    SESSION_COOKIE_SECURE = False

    # --- how check-in works ---
    # True  (kiosk): one shared camera by a door. Anyone enrolled who is recognised gets
    #                marked present, whoever happens to be signed in on the station. The
    #                account is the operator, not the person being recorded.
    # False (personal): each person signs into their own account and checks themselves
    #                in. A recognised face that is not the signed-in account is refused,
    #                which is what makes "only the registered person can check in" true.
    #                Requires each account to be linked to an enrolled person by an admin.
    #
    # read from the environment at import, which is safe here in a way it was not for the
    # secret key: this reads a flag, it does not generate or write anything
    KIOSK_MODE = env_flag("KIOSK_MODE", default=True)

    # --- anti-spoofing ---
    # OFF by default, which deserves an explanation because a security control that
    # defaults to off is usually a mistake.
    #
    # It is off because the right threshold depends on the camera and the lighting, and
    # nobody can pick it for you from the outside. Measured on one webcam with
    # scripts/calibrate_liveness.py, 40 samples each:
    #
    #     real faces     -3.82 .. +5.04   median +1.63
    #     photo / screen -11.06 .. -2.39  median -6.72
    #
    # which separates cleanly at the default threshold of 0.0: no spoof got through, with
    # 2.39 of margin, at the cost of about 20% of frames of a real person being refused.
    # That cost is largely absorbed by the camera retrying every 1.5s, and the trade is
    # deliberately lopsided -- a false reject costs a second, a false accept costs the
    # entire point of the feature, and an attacker holding a photo up gets to retry too.
    #
    # Run the calibration on your own camera before switching this on, and re-run it if
    # you move the setup or change hardware.
    LIVENESS_ENABLED = env_flag("LIVENESS_ENABLED", default=False)

    # Threshold on the model's real-minus-spoof logit difference. 0.0 means "whichever
    # the model leans towards"; positive values demand more confidence before accepting
    # a face. The same shape of trade-off as TOLERANCE, pointing the other way: raise it
    # and photographs stop getting through while real people start being refused.
    # There is no universally right value -- it depends on the camera and the lighting.
    LIVENESS_THRESHOLD = env_float("LIVENESS_THRESHOLD", default=0.0)

    # the secret key signs session cookies. it is a classmethod rather than a plain
    # attribute so that nothing is read from the environment (or written to disk) until
    # an app is actually being built -- importing this module has no side effects.
    @classmethod
    def secret_key(cls):
        return os.environ.get("FLASK_SECRET_KEY") or generate_dev_secret_key()


class DevelopmentConfig(Config):
    DEBUG = True


class TestingConfig(Config):
    TESTING = True

    # Off unless a test asks for it. Two reasons, both practical: the generated images
    # tests use contain no face, so the model would call every one of them a spoof and
    # fail tests that are about something else entirely; and running real inference in
    # every test took the suite from ~80 seconds to ~18 minutes.
    #
    # The gate itself is covered directly in tests/test_liveness.py, which turns it back
    # on and stubs the verdict. Whether it defaults to ON in production is asserted
    # against Config, not against this class.
    LIVENESS_ENABLED = False

    @classmethod
    def secret_key(cls):
        # fixed and obviously fake: tests must never depend on the developer's real key,
        # and must never create the .flask_secret_dev file as a side effect
        return "testing-key-not-for-production"


class ProductionConfig(Config):

    # only send the session cookie over https. without this, one plain-http request --
    # a bookmark, a typed address, a link in an email -- leaks the session cookie in
    # cleartext, and the cookie is the whole login.
    #
    # note this makes the app unusable over plain http, which is the point: if it stops
    # working after deployment, the fix is to terminate TLS in front of it, not to turn
    # this back off.
    SESSION_COOKIE_SECURE = True

    @classmethod
    def secret_key(cls):
        # no fallback here on purpose. anyone who knows the key can forge a session
        # cookie that claims role=admin, so a missing key has to stop the app starting
        # rather than quietly degrade into a guessable default.
        key = os.environ.get("FLASK_SECRET_KEY")
        if not key:
            raise RuntimeError(
                "FLASK_SECRET_KEY must be set in production. Generate one with:\n"
                '    python -c "import secrets; print(secrets.token_hex(32))"'
            )
        return key


def get_config():
    """Pick a config class from the environment."""
    if os.environ.get("FLASK_ENV") == "production":
        return ProductionConfig
    return DevelopmentConfig
