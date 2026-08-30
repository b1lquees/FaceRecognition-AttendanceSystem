"""Settings for each environment the app runs in.

Splitting them into classes lets the tests build an app that never touches the real
database or the real secret key, without setting environment variables first.
"""

import logging  # to produce a warning if flask_env var is used
import os  # to read environment variables
import secrets  # cryptographically secure random secret key
from datetime import timedelta
from pathlib import Path

# config.py lives in attendance/, so the project root is one level up
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEV_KEY_FILE = PROJECT_ROOT / ".flask_secret_dev" # stores the generated developement secret key

# tuple containing values that should be interpreted as false will become useful when reading env
# var
FALSY = ("0", "false", "no", "off")


def env_flag(name, default):
    """Read a boolean setting from the environment.

    An unset variable and an empty one both mean "use the default". That second case is
    not hypothetical: `setx NAME ""` is how people clear a variable on Windows, and it
    leaves an empty string behind rather than removing it. Treating empty as truthy --
    which a bare `value not in FALSY` does -- meant the ordinary way to turn a setting
    off would turn it on instead.
    """
    # asks if an env var w that name exists if it doesnt use default value else convert the string
    # to boolean
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


def env_int(name, default):
    """Read a whole-number setting, refusing to guess at a typo.

    Same reasoning as env_float. It matters more here than for a threshold: the number
    this reads is how many proxies are trusted, and falling back to a default because
    somebody typed "one" would change who the rate limiter thinks it is talking to
    without saying so.
    """
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        raise RuntimeError(
            f"{name} must be a whole number, got {raw!r}"
        ) from None


def env_path(name, default):
    """Read a filesystem path from the environment, falling back to a default.

    The three things this application writes -- the database, the enrolment photos and
    the encoding cache -- all sat next to the source code. That is right for a checkout
    and wrong for a container, where the source lives in an image that is thrown away and
    rebuilt while the data has to outlive it. Each of them now names an environment
    variable so a deployment can point it somewhere that persists; unset, every one of
    them still resolves exactly where it always did.

    Empty means unset, for the same reason as env_flag: `setx NAME ""` is how a variable
    gets cleared on Windows, and it leaves an empty string behind. Reading that as a path
    would send the data to the process's working directory.
    """
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return Path(default)
    return Path(raw.strip())


def generate_dev_secret_key():
    """Return a development key, generating and caching one on first use.

    Caching matters because the debug reloader restarts the process every time you
    save a file. A fresh key each restart would invalidate the session cookie and
    log you out constantly.
    """
    # read_text() reads the contents of a text file methods of Path object write_text does the
    # opposite writes a string into text file
    if DEV_KEY_FILE.exists():
        return DEV_KEY_FILE.read_text().strip()
    # if it doesnt exist
    key = secrets.token_hex(32)  # generate a cryptographically secure random bytes as hex
    DEV_KEY_FILE.write_text(key) # write it to .flask_secret_dev
    print("No FLASK_SECRET_KEY set - generated a development key in .flask_secret_dev")
    return key
# storing it instead of generating a new key everytime is important flask's session cookie is signed
# using secret key. if u generated a key evrytime restart server new secret key old session cookie
# no longer valid user gets logged out


class Config: # base configuration contains settings shared across environment
    """Settings shared by every environment."""

    DEBUG = False
    TESTING = False

    # a webcam frame is roughly 50-100 KB as base64. anything far above that is either a bug or an
    # attempt to exhaust the server's memory, so Flask rejects the request with 413 before reading
    # the body.
    MAX_CONTENT_LENGTH = 8 * 1024 * 1024  # 8 MB

    # session cookie hardening
    # HttpOnly keeps JavaScript running in the browser from reading the session cookie, so an
    # injected script cannot steal a session. Flask defaults this to True already it is stated
    # explicitly because a security-relevant default is worth being visible rather than assumed.
    SESSION_COOKIE_HTTPONLY = True

    # Lax stops the cookie being sent on cross-site POSTs, which is a second line of defence behind
    # the CSRF tokens in security.py. Strict would also drop it when following an ordinary link from
    # another site, which would log people out for no security gain here. CROSS-Site Link Clicks GET
    # allowed in lax , cross-sit forms post and subresources not allowed
    SESSION_COOKIE_SAMESITE = "Lax"

    # False by default so the cookie still works over plain http in development. ProductionConfig
    # turns it on will only send cookies over https or else an attacker on the network could
    # potentially interfcept the cookie while https encryots the communication
    SESSION_COOKIE_SECURE = False

    # How long a signed session cookie stays valid, counted from when it was issued.
    #
    # Stated here because it was already in force and invisible: Flask defaults it to 31
    # days and enforces it server-side -- open_session() passes this as max_age when it
    # unseals the cookie -- so the choice was being made by a framework default nobody had
    # read. Seven days is a deliberate value rather than a better one.
    #
    # What it buys, honestly: it caps how long a leaked cookie stays useful. It is not a
    # revocation mechanism and cannot be made into one. These are signed cookies, so there
    # is no server-side session to invalidate, and the only way to kill one early is to
    # rotate FLASK_SECRET_KEY -- which signs out everybody, camera station included.
    #
    # Why days rather than hours. Sessions here are not permanent, so this is an ABSOLUTE
    # ceiling measured from login, not an idle timeout: traffic does not refresh it. A
    # short ceiling would therefore expire the door camera mid-use, on a schedule unrelated
    # to whether anybody is standing at it -- and that failure is worse than it sounds,
    # because a register with no rows looks exactly like a room with no people. A week is
    # long enough that the station is restarted for other reasons more often than this
    # fires. See camera.html for what the page does when it does fire.
    PERMANENT_SESSION_LIFETIME = timedelta(days=7)

    # what sits in front of this application How many reverse proxies a request passes through
    # before it reaches us. 0 means none, and request.remote_addr is the client's own address.
    #
    # Behind a proxy remote_addr is the *proxy's* address, identically for everybody. That is not a
    # corner case: production sets SESSION_COOKIE_SECURE, so it needs TLS terminated in front of it,
    # so it is always behind one. Left uncorrected, every limit in ratelimit.py collapses into a
    # single shared bucket and every line of the audit trail records the same address.
    #
    # It has to be configured rather than assumed, because the number is load-bearing in the other
    # direction too. X-Forwarded-For is written by the client and appended to by each hop, so
    # ProxyFix counts entries from the right and believes exactly this many. Setting it higher than
    # the number of proxies actually in front lets a client prepend any address they like and be
    # believed -- a fresh rate-limit allowance per request, and a forged address in the log. Too low
    # is merely useless; too high is worse than not doing this at all. So: default 0, and state the
    # real number in the deployment.
    TRUSTED_PROXY_HOPS = env_int("TRUSTED_PROXY_HOPS", default=0)

    # how check-in works
    # True (kiosk): one shared camera by a door. Anyone enrolled who is recognised gets marked
    # present, whoever happens to be signed in on the station. The account is the operator, not the
    # person being recorded. False (personal): each person signs into their own account and checks
    # themselves in. A recognised face that is not the signed-in account is refused, which is what
    # makes "only the registered person can check in" true. Requires each account to be linked to an
    # enrolled person by an admin.

    # read from the environment at import, which is safe here in a way it was not for the secret
    # key: this reads a flag, it does not generate or write anything
    KIOSK_MODE = env_flag("KIOSK_MODE", default=True)

    # anti-spoofing
    # OFF by default, which deserves an explanation because a security control that defaults to off
    # is usually a mistake. It is off because the right threshold depends on the camera and the
    # lighting, and nobody can pick it for you from the outside. Measured on one webcam with
    # scripts/calibrate_liveness.py, 40 samples each: real faces -3.82 .. +5.04 median +1.63 photo /
    # screen -11.06 .. -2.39 median -6.72 which separates cleanly at the default threshold of 0.0:
    # no spoof got through, with 2.39 of margin, at the cost of about 20% of frames of a real person
    # being refused. That cost is largely absorbed by the camera retrying every 1.5s, and the trade
    # is deliberately lopsided -- a false reject costs a second, a false accept costs the entire
    # point of the feature, and an attacker holding a photo up gets to retry too. Run the
    # calibration on your own camera before switching this on, and re-run it if you move the setup
    # or change hardware.
    LIVENESS_ENABLED = env_flag("LIVENESS_ENABLED", default=False)

    # Threshold on the model's real-minus-spoof logit difference. 0.0 means "whichever the model
    # leans towards"; positive values demand more confidence before accepting a face. The same shape
    # of trade-off as TOLERANCE, pointing the other way: raise it and photographs stop getting
    # through while real people start being refused. There is no universally right value it depends
    # on the camera and the lighting.
    LIVENESS_THRESHOLD = env_float("LIVENESS_THRESHOLD", default=0.0)
    #  time
    # TIMEZONE is deliberately NOT a setting here, and this note is the sign saying so.
    #
    # It used to be, and nothing read it. clock.get_timezone() goes to the environment directly, so
    # the attribute sat here looking authoritative while having no effect whatsoever -- setting
    # app.config["TIMEZONE"] changed nothing, which is a worse trap than the setting simply not
    # existing.
    #
    # It stays in the environment because clock.py is imported by the command-line scripts too, and
    # those have no application to read config from. One source, read the same way everywhere. The
    # variable itself is unchanged and still documented in the README: an IANA zone name
    # ("Europe/London", "Asia/Karachi"), unset meaning the machine's own. logging INFO because the
    # audit trail is logged at INFO; raising this to WARNING keeps the errors and throws away the
    # record of who approved, enrolled or linked whom.
    LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")
    # Unset means stderr only, which is what a container or systemd wants -- writing only to a file
    # assumes something is there to read it. Set a path to also keep a rotating copy on disk.
    LOG_FILE = os.environ.get("LOG_FILE") or None
    # the secret key signs session cookies. it is a classmethod rather than a plain attribute so
    # that nothing is read from the environment (or written to disk) until an app is actually being
    # built -- importing this module has no side effects.
    @classmethod # belongs to class rather than requiring an object to be created first
    def secret_key(cls):
        return os.environ.get("FLASK_SECRET_KEY") or generate_dev_secret_key()

class DevelopmentConfig(Config): # inherits everything from Config and only changes debug here
    DEBUG = True

class TestingConfig(Config):
    TESTING = True

    # Off unless a test asks for it. Two reasons, both practical: the generated images tests use
    # contain no face, so the model would call every one of them a spoof and fail tests that are
    # about something else entirely; and running real inference in every test took the suite from
    # ~80 seconds to ~18 minutes.
    #
    # The gate itself is covered directly in tests/test_liveness.py, which turns it back on and
    # stubs the verdict. Whether it defaults to ON in production is asserted against Config, not
    # against this class.
    LIVENESS_ENABLED = False

    @classmethod
    def secret_key(cls):
        # fake: tests must never depend on the developer's real key,and must never create the
        # .flask_secret_dev file as a side effect
        return "testing-key-not-for-production"


class ProductionConfig(Config):

    # only send the session cookie over https. without this, one plain-http request, a bookmark, a
    # typed address, a link in an email leaks the session cookie in cleartext, and the cookie is the
    # whole login. note this makes the app unusable over plain http, which is the point: if it stops
    # working after deployment, the fix is to terminate TLS in front of it, not to turn this back
    # off.
    SESSION_COOKIE_SECURE = True

    @classmethod
    def secret_key(cls):
        # no fallback here on purpose. anyone who knows the key can forge a session cookie that
        # claims role=admin, so a missing key has to stop the app starting rather than quietly
        # degrade into a guessable default.
        key = os.environ.get("FLASK_SECRET_KEY")
        if not key:
            raise RuntimeError(
                "FLASK_SECRET_KEY must be set in production. Generate one with:\n"
                '    python -c "import secrets; print(secrets.token_hex(32))"'
            )
        return key


def get_config():
    """Pick a config class from the environment.

    The variable is APP_ENV, not FLASK_ENV. Flask deprecated FLASK_ENV in 2.2 and removed
    it in 2.3, so on the Flask this project pins the name means nothing to the framework
    -- it only ever meant what this function chose to make it mean, while looking like a
    setting Flask itself would act on.
    """
    env = os.environ.get("APP_ENV")
    if env is None:
        env = os.environ.get("FLASK_ENV")
        if env is not None:
            logging.getLogger(__name__).warning(
                "FLASK_ENV is deprecated and will stop being read in a future release; "
                "set APP_ENV=%s instead.",
                env,
            )
    return ProductionConfig if (env or "").strip().lower() == "production" else DevelopmentConfig
