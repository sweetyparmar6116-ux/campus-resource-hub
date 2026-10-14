import os
import re
import sqlite3
from datetime import date, timedelta

import pytest

import app as app_module
from conftest import ADMIN, STUDENT, csrf, db, login, logout, post, register

FUTURE = (date.today() + timedelta(days=7)).isoformat()
SLOT = "09:00–11:00"


def book(client, resource_id=1, booking_date=FUTURE, slot=SLOT, purpose="Project work"):
    return post(client, "/book", {"resource_id": resource_id, "booking_date": booking_date,
                                  "time_slot": slot, "purpose": purpose})


def status_of(booking_id):
    conn = db()
    try:
        return conn.execute("SELECT status FROM bookings WHERE id=?", (booking_id,)).fetchone()["status"]
    finally:
        conn.close()


@pytest.fixture()
def student(client):
    register(client)
    login(client, STUDENT)
    return client


@pytest.fixture()
def admin(client):
    login(client, ADMIN)
    return client


# 1-2. start-up and database -------------------------------------------------
def test_app_starts_and_health_ok(client):
    response = client.get("/health")
    assert response.status_code == 200 and response.get_json()["status"] == "ok"


def test_database_initialises_and_is_idempotent(client):
    conn = db()
    tables = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"users", "resources", "bookings"} <= tables
    resources_before = conn.execute("SELECT COUNT(*) FROM resources").fetchone()[0]
    conn.close()
    register(client)
    app_module.init_db()  # second run must not duplicate or delete anything
    conn = db()
    assert conn.execute("SELECT COUNT(*) FROM resources").fetchone()[0] == resources_before
    assert conn.execute("SELECT COUNT(*) FROM users WHERE role='admin'").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM users WHERE role='student'").fetchone()[0] == 1
    conn.close()


def test_passwords_are_hashed(client):
    register(client)
    conn = db()
    stored = conn.execute("SELECT password_hash FROM users WHERE email=?", (STUDENT[0],)).fetchone()[0]
    conn.close()
    assert STUDENT[1] not in stored and stored.startswith(("scrypt:", "pbkdf2:"))


# 3-5. login and role redirects ---------------------------------------------
def test_student_register_login_and_dashboard(client):
    assert register(client).headers["Location"].endswith("/login")
    response = login(client, STUDENT)
    assert response.status_code == 302 and response.headers["Location"].endswith("/dashboard")
    page = client.get("/dashboard")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert "STUDENT DASHBOARD" in html and "Student</span>" in html and "Administrator</span>" not in html


def test_admin_login_goes_to_admin_dashboard(client):
    response = login(client, ADMIN)
    assert response.headers["Location"].endswith("/admin")
    html = client.get("/admin").get_data(as_text=True)
    assert "Booking <em>requests.</em>" in html and "Administrator</span>" in html
    assert client.get("/dashboard").headers["Location"].endswith("/admin")  # admin never sees student page


def test_admin_login_page_works_and_rejects_students(client):
    register(client)
    refused = login(client, STUDENT, url="/admin/login")
    assert refused.status_code == 200 and "not an administrator account" in refused.get_data(as_text=True)
    assert client.get("/admin").status_code == 302  # still not signed in
    ok = login(client, ADMIN, url="/admin/login")
    assert ok.headers["Location"].endswith("/admin")


def test_wrong_password_and_unknown_email(client):
    register(client)
    for creds in [(STUDENT[0], "wrong-password"), ("nobody@example.com", "whatever123")]:
        response = login(client, creds)
        assert response.status_code == 200 and "incorrect" in response.get_data(as_text=True)


# 6-7. authorisation ----------------------------------------------------------
@pytest.mark.parametrize("url", ["/admin", "/dashboard", "/api/availability?resource_id=1&date=2030-01-01"])
def test_anonymous_cannot_open_protected_pages(client, url):
    response = client.get(url)
    assert response.status_code == 302 and "/login" in response.headers["Location"]


def test_anonymous_cannot_post_protected_actions(client):
    token = csrf(client)
    for url in ["/book", "/booking/1/cancel", "/admin/booking/1/approve", "/admin/resource", "/admin/resource/1/toggle", "/logout"]:
        response = client.post(url, data={"csrf_token": token})
        assert response.status_code == 302 and "/login" in response.headers["Location"], url


def test_student_cannot_use_admin_routes(student):
    assert book(student).status_code == 302
    page = student.get("/admin")
    assert page.status_code == 302 and page.headers["Location"].endswith("/dashboard")
    assert "administrators only" in student.get("/dashboard").get_data(as_text=True)
    assert post(student, "/admin/booking/1/approve").headers["Location"].endswith("/dashboard")
    assert status_of(1) == "Pending"
    post(student, "/admin/resource", {"name": "Hack", "category": "Hall", "location": "x", "capacity": 1})
    post(student, "/admin/resource/1/toggle")
    conn = db()
    assert conn.execute("SELECT COUNT(*) FROM resources WHERE name='Hack'").fetchone()[0] == 0
    assert conn.execute("SELECT active FROM resources WHERE id=1").fetchone()[0] == 1
    conn.close()


def test_role_cannot_be_injected_through_forms(client):
    register(client, role="admin", is_admin="1")
    conn = db()
    assert conn.execute("SELECT role FROM users WHERE email=?", (STUDENT[0],)).fetchone()[0] == "student"
    conn.close()
    login(client, STUDENT)
    assert client.get("/admin").status_code == 302


def test_admin_cannot_create_bookings_as_student(admin):
    response = book(admin)
    assert response.headers["Location"].endswith("/admin")
    conn = db()
    assert conn.execute("SELECT COUNT(*) FROM bookings").fetchone()[0] == 0
    conn.close()


# 8. student bookings ---------------------------------------------------------
def test_student_creates_and_sees_own_booking(student):
    assert book(student).headers["Location"].endswith("/dashboard")
    html = student.get("/dashboard").get_data(as_text=True)
    assert "Innovation Lab" in html and FUTURE in html and "status-pending" in html


def test_students_only_see_and_cancel_their_own_bookings(client):
    register(client)
    login(client, STUDENT)
    book(client, purpose="Secret purpose of student one")
    logout(client)
    other = ("other@example.com", "other-pass-1234")
    register(client, name="Other Student", email=other[0], password=other[1])
    login(client, other)
    html = client.get("/dashboard").get_data(as_text=True)
    assert "Secret purpose" not in html and "Innovation Lab" not in html
    post(client, "/booking/1/cancel")
    assert status_of(1) == "Pending"  # someone else's booking is untouched


def test_availability_api_lists_taken_slots_only(student):
    book(student)
    data = student.get(f"/api/availability?resource_id=1&date={FUTURE}").get_json()
    assert data == {"taken": [SLOT]}
    assert student.get("/api/availability?resource_id=1&date=nonsense").status_code == 400


def test_duplicate_slot_is_blocked_and_freed_after_cancel(client):
    register(client)
    login(client, STUDENT)
    book(client)
    book(client)  # same slot again
    conn = db()
    assert conn.execute("SELECT COUNT(*) FROM bookings").fetchone()[0] == 1
    conn.close()
    post(client, "/booking/1/cancel")
    assert status_of(1) == "Cancelled"
    book(client)
    conn = db()
    assert conn.execute("SELECT COUNT(*) FROM bookings WHERE status='Pending'").fetchone()[0] == 1
    conn.close()


def test_database_blocks_duplicate_active_bookings(client):
    register(client)
    login(client, STUDENT)
    book(client)
    conn = db()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO bookings(user_id,resource_id,booking_date,time_slot,purpose) VALUES(2,1,?,?,'x')",
                     (FUTURE, SLOT))
    conn.close()


# 9-10. admin review and cancellation -----------------------------------------
def test_admin_approves_rejects_and_cancels(client):
    register(client)
    login(client, STUDENT)
    book(client, slot="09:00–11:00")
    book(client, slot="11:00–13:00")
    book(client, slot="13:00–15:00")
    logout(client)
    login(client, ADMIN)
    page = client.get("/admin").get_data(as_text=True)
    assert "student@example.com" in page and "Approve" in page and "Pending (3)" in page
    post(client, "/admin/booking/1/approve")
    post(client, "/admin/booking/2/reject")
    assert (status_of(1), status_of(2), status_of(3)) == ("Approved", "Rejected", "Pending")
    post(client, "/admin/booking/1/approve")  # already approved: no change, friendly message
    assert "already approved" in client.get("/admin").get_data(as_text=True)
    post(client, "/admin/booking/1/cancel")
    assert status_of(1) == "Cancelled"
    post(client, "/admin/booking/2/cancel")  # rejected bookings can't be cancelled
    assert status_of(2) == "Rejected"
    assert client.post("/admin/booking/3/explode", data={"csrf_token": csrf(client)}).status_code == 302
    assert status_of(3) == "Pending"
    assert "Pending (1)" in client.get("/admin?status=Pending").get_data(as_text=True)
    filtered = client.get("/admin?status=Rejected").get_data(as_text=True)
    assert "Rejected (1)" in filtered


def test_student_sees_updated_status_and_can_cancel_approved(client):
    register(client)
    login(client, STUDENT)
    book(client)
    logout(client)
    login(client, ADMIN)
    post(client, "/admin/booking/1/approve")
    logout(client)
    login(client, STUDENT)
    assert "status-approved" in client.get("/dashboard").get_data(as_text=True)
    post(client, "/booking/1/cancel")
    assert status_of(1) == "Cancelled"


def test_past_booking_cannot_be_cancelled(student):
    conn = db()
    uid = conn.execute("SELECT id FROM users WHERE email=?", (STUDENT[0],)).fetchone()[0]
    conn.execute("INSERT INTO bookings(user_id,resource_id,booking_date,time_slot,purpose,status) "
                 "VALUES(?,1,'2020-01-01','09:00–11:00','old','Approved')", (uid,))
    conn.commit(); conn.close()
    post(student, "/booking/1/cancel")
    assert status_of(1) == "Approved"


def test_admin_manages_resources(admin):
    post(admin, "/admin/resource", {"name": "Design Studio", "category": "Classroom", "location": "Block D",
                                    "capacity": 30, "description": "Creative space", "icon": "🎨"})
    conn = db()
    row = conn.execute("SELECT * FROM resources WHERE name='Design Studio'").fetchone()
    assert row and row["active"] == 1
    post(admin, f"/admin/resource/{row['id']}/toggle")
    assert conn.execute("SELECT active FROM resources WHERE id=?", (row["id"],)).fetchone()[0] == 0
    conn.close()
    assert "Design Studio" not in admin.get("/resources").get_data(as_text=True)
    post(admin, "/admin/resource", {"name": "", "category": "Nope", "location": "x", "capacity": -5})
    assert "Complete all resource fields" in admin.get("/admin").get_data(as_text=True)


# 11. invalid forms -----------------------------------------------------------
@pytest.mark.parametrize("fields,message", [
    (dict(name="", email="a@example.com", password="longenough1", confirm_password="longenough1"), "Enter your name"),
    (dict(name="A", email="not-an-email", password="longenough1", confirm_password="longenough1"), "valid email"),
    (dict(name="A", email="a@example.com", password="short", confirm_password="short"), "8 to 128"),
    (dict(name="A", email="a@example.com", password="longenough1", confirm_password="different11"), "do not match"),
])
def test_registration_validation_messages(client, fields, message):
    response = post(client, "/register", fields)
    assert response.status_code == 200 and message in response.get_data(as_text=True)


def test_registration_keeps_typed_values_and_blocks_duplicate_email(client):
    register(client)
    response = register(client, name="Someone Else")
    html = response.get_data(as_text=True)
    assert "already exists" in html and 'value="Someone Else"' in html


@pytest.mark.parametrize("kwargs", [
    dict(booking_date="2020-01-01"), dict(booking_date="garbage"), dict(slot="03:00–04:00"),
    dict(purpose="   "), dict(purpose="x" * 301), dict(resource_id=9999),
])
def test_invalid_booking_is_rejected_with_message(student, kwargs):
    response = book(student, **kwargs)
    assert response.headers["Location"].endswith("/resources")
    assert "Check the resource" in student.get("/resources").get_data(as_text=True)
    conn = db()
    assert conn.execute("SELECT COUNT(*) FROM bookings").fetchone()[0] == 0
    conn.close()


# security --------------------------------------------------------------------
def test_csrf_blocks_posts_without_a_valid_token(client):
    register(client)
    login(client, STUDENT)
    assert client.post("/book", data={"resource_id": 1}).status_code == 400
    assert client.post("/book", data={"resource_id": 1, "csrf_token": "forged"}).status_code == 400
    assert client.post("/logout").status_code == 400
    assert "expired" in client.post("/logout").get_data(as_text=True)
    assert client.get("/dashboard").status_code == 200  # still signed in


def test_login_requires_csrf_token(client):
    assert client.post("/login", data={"email": ADMIN[0], "password": ADMIN[1]}).status_code == 400


@pytest.mark.parametrize("bad_next", ["//evil.example", "https://evil.example", "/\\evil.example", "javascript:alert(1)"])
def test_login_next_cannot_redirect_off_site(client, bad_next):
    register(client)
    response = login(client, STUDENT, url=f"/login?next={bad_next}")
    assert response.headers["Location"].endswith("/dashboard")


def test_login_next_allows_local_paths(client):
    register(client)
    response = login(client, STUDENT, url="/login?next=/resources")
    assert response.headers["Location"] == "/resources"


def test_logout_ends_session_and_get_is_not_allowed(client):
    register(client)
    login(client, STUDENT)
    assert client.get("/logout").status_code == 405
    assert logout(client).status_code == 302
    assert client.get("/dashboard").status_code == 302


def test_private_pages_are_not_cached(student):
    assert "no-store" in student.get("/dashboard").headers["Cache-Control"]


def test_production_mode_refuses_unsafe_configuration(monkeypatch):
    monkeypatch.setattr(app_module, "IS_PRODUCTION", True)
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    with pytest.raises(RuntimeError):
        app_module.admin_settings()
    monkeypatch.setenv("SECRET_KEY", "replace-with-a-long-random-secret")
    with pytest.raises(RuntimeError):
        app_module.load_secret_key()


# 12. no personal details ---------------------------------------------------------
def test_no_personal_name_or_email_left_in_project_files():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    # built from pieces so this test file does not match itself
    patterns = [re.compile(p, re.I) for p in ("swee" + "ty", "par" + "mar", "@gm" + "ail", "61" + "16")]
    skip_dirs = {".git", "__pycache__", ".pytest_cache", "instance", ".venv", "venv"}
    hits = []
    for folder, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in skip_dirs]
        for name in files:
            if name.endswith((".db", ".png", ".jpg", ".zip", ".pyc")):
                continue
            path = os.path.join(folder, name)
            text = open(path, encoding="utf-8", errors="ignore").read()
            hits += [f"{os.path.relpath(path, root)}: {p.pattern}" for p in patterns if p.search(text)]
    assert not hits, hits


def test_placeholders_are_neutral(client):
    html = client.get("/register").get_data(as_text=True)
    assert 'placeholder="Student Name"' in html and 'placeholder="student@example.com"' in html


# 13. pages and static files render ---------------------------------------------------
@pytest.mark.parametrize("url", ["/", "/resources", "/resources?q=lab&category=Laboratory", "/intro",
                                 "/login", "/register", "/admin/login"])
def test_public_pages_render(client, url):
    assert client.get(url).status_code == 200


def test_static_files_and_template_references(client):
    for url in ("/static/styles.css", "/static/app.js"):
        assert client.get(url).status_code == 200
    home = client.get("/").get_data(as_text=True)
    assert "/static/styles.css" in home and "/static/app.js" in home and "/intro" in home  # intro is reachable


def test_error_pages(client):
    assert client.get("/no-such-page").status_code == 404
    assert "find that page" in client.get("/no-such-page").get_data(as_text=True)
    assert client.get("/book").status_code == 405


def test_every_template_page_renders_for_each_role(client):
    register(client)
    login(client, STUDENT)
    book(client)
    for url in ("/dashboard", "/resources", "/"):
        assert client.get(url).status_code == 200
    logout(client)
    login(client, ADMIN)
    for url in ("/admin", "/admin?status=Pending", "/resources", "/"):
        assert client.get(url).status_code == 200
