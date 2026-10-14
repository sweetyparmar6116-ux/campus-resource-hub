"""Shared test setup. Tests use a throw-away database and never touch campushub.db."""
import os
import re
import sys
import tempfile

# Must be set BEFORE app.py is imported (environment variables beat any local .env file).
_tmp = tempfile.mkdtemp()
os.environ.update(
    APP_ENV="development",
    SECRET_KEY="test-only-secret-key-not-for-real-use-0123456789",
    ADMIN_EMAIL="admin@example.com",
    ADMIN_PASSWORD="Test-Admin-Pass-1",
    DATABASE_PATH=os.path.join(_tmp, "import.db"),
)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

import app as app_module  # noqa: E402

ADMIN = ("admin@example.com", "Test-Admin-Pass-1")
STUDENT = ("student@example.com", "student-pass-123")


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "DATABASE", str(tmp_path / "test.db"))
    app_module.init_db()
    app_module.app.config["TESTING"] = True
    return app_module.app.test_client()


def csrf(client):
    """Fetch a valid CSRF token for the client's current session."""
    for page in ("/login", "/resources"):
        response = client.get(page)
        match = re.search(r'name="csrf_token" value="([^"]+)"', response.get_data(as_text=True))
        if match:
            return match.group(1)
    raise AssertionError("no CSRF token found on page")


def post(client, url, data=None, **kwargs):
    data = dict(data or {})
    data.setdefault("csrf_token", csrf(client))
    return client.post(url, data=data, **kwargs)


def register(client, name="Student Name", email=STUDENT[0], password=STUDENT[1], **extra):
    return post(client, "/register", dict(name=name, email=email, password=password,
                                          confirm_password=password, **extra))


def login(client, creds, url="/login", **kwargs):
    return post(client, url, {"email": creds[0], "password": creds[1]}, **kwargs)


def logout(client):
    return post(client, "/logout")


def db():
    import sqlite3
    conn = sqlite3.connect(app_module.DATABASE)
    conn.row_factory = sqlite3.Row
    return conn
