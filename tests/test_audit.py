import logging

import pytest

from attendance.audit import audit, configure_logging
from attendance.auth_db import create_user, list_pending_users, register_pending_user


@pytest.fixture
def logs(app, caplog):
    """Capture what the application logs during a test."""
    caplog.set_level(logging.INFO, logger=app.logger.name)
    return caplog


def audit_lines(logs):
    return [r.getMessage() for r in logs.records if "audit action=" in r.getMessage()]


# --- the record exists at all ----------------------------------------------------

# the gap this closes: the application had access control and no record of it being
# used. "who let this person in" had no answer.
def test_approving_an_account_is_recorded(client, login, csrf, logs):
    register_pending_user("newperson", "password123")
    user_id = list_pending_users()[0][0]
    login(username="admin1", password="admin-password", role="admin")

    client.post(f"/admin/users/{user_id}/approve", data={"_csrf_token": csrf})

    line = next(line for line in audit_lines(logs) if "account.approved" in line)
    assert "by=admin1" in line
    assert f"target_id={user_id}" in line


def test_rejecting_an_account_is_recorded(client, login, csrf, logs):
    register_pending_user("spammer", "password123")
    user_id = list_pending_users()[0][0]
    login(username="admin1", password="admin-password", role="admin")

    client.post(f"/admin/users/{user_id}/reject", data={"_csrf_token": csrf})

    assert any("account.rejected" in line for line in audit_lines(logs))


def test_a_successful_login_is_recorded(client, csrf, logs):
    create_user("alice", "a-good-password", role="admin")

    client.post(
        "/login",
        data={"username": "alice", "password": "a-good-password", "_csrf_token": csrf},
    )

    line = next(line for line in audit_lines(logs) if "login.success" in line)
    assert "account=alice" in line
    assert "role=admin" in line


# repeated lines from one address are what a brute-force attempt looks like from here
def test_a_failed_login_is_recorded_with_the_attempted_username(client, csrf, logs):
    create_user("alice", "the-real-password")

    client.post(
        "/login",
        data={"username": "alice", "password": "wrong", "_csrf_token": csrf},
    )

    line = next(line for line in audit_lines(logs) if "login.failed" in line)
    assert "account=alice" in line


# --- what must never be written --------------------------------------------------

# a log that leaks the thing it protects is worse than no log, and logs get copied to
# places the database never goes
@pytest.mark.parametrize("password", ["the-real-password", "wrong-password"])
def test_a_password_is_never_logged(client, csrf, logs, password):
    create_user("alice", "the-real-password")

    client.post(
        "/login",
        data={"username": "alice", "password": password, "_csrf_token": csrf},
    )

    everything = " ".join(r.getMessage() for r in logs.records)
    assert password not in everything


def test_the_csrf_token_is_never_logged(client, login, csrf, logs):
    login(username="admin1", password="admin-password", role="admin")

    everything = " ".join(r.getMessage() for r in logs.records)
    assert csrf not in everything


# --- the helper itself -----------------------------------------------------------

def test_audit_records_the_actor_and_the_details(app, logs):
    with app.test_request_context("/"):
        audit("something.happened", thing="a-value")

    line = audit_lines(logs)[0]
    assert "action=something.happened" in line
    assert "thing=a-value" in line
    assert "by=anonymous" in line  # no session in this context


# audit() is called from error paths too. a logging call that raised would turn a handled
# problem into an unhandled one.
def test_audit_outside_a_request_does_not_raise(app, logs):
    with app.app_context():
        audit("background.thing")

    assert "by=-" in audit_lines(logs)[0]


# --- configuration ---------------------------------------------------------------

def test_logging_is_configured_so_a_production_server_is_not_silent(app):
    # Flask only attaches its own handler in debug mode
    assert app.logger.handlers
    assert app.logger.level <= logging.INFO


def test_a_log_file_is_written_when_one_is_configured(tmp_path, app):
    log_file = tmp_path / "attendance.log"
    app.config["LOG_FILE"] = str(log_file)
    app.config["LOG_LEVEL"] = "INFO"

    configure_logging(app)
    with app.test_request_context("/"):
        audit("written.to.file", marker="present")
    for handler in app.logger.handlers:
        handler.flush()

    assert "written.to.file" in log_file.read_text(encoding="utf-8")


# werkzeug logs one line per request, and /recognize is posted to every 1.5 seconds --
# at INFO it buries the audit trail in its own noise
def test_the_request_log_is_quietened(app):
    assert logging.getLogger("werkzeug").level >= logging.WARNING
