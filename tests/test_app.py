import re
from datetime import date, datetime, timedelta

import pytest
import app as campus_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("ADMIN_EMAIL", "admin@example.test")
    monkeypatch.setenv("ADMIN_PASSWORD", "admin-test-password")
    monkeypatch.setattr(campus_app, "DATABASE", str(tmp_path / "campushub-test.db"))
    campus_app.app.config.update(TESTING=True, SECRET_KEY="test-session-secret")
    campus_app.init_db()
    with campus_app.app.test_client() as test_client:
        yield test_client


def csrf_token(response):
    match = re.search(r'name="csrf_token" value="([^"]+)"', response.get_data(as_text=True))
    assert match, "rendered form did not include a CSRF token"
    return match.group(1)


def register_student(client, email="student@example.test"):
    response = client.get("/register")
    return client.post(
        "/register",
        data={
            "csrf_token": csrf_token(response),
            "name": "Test Student",
            "email": email,
            "password": "student-test-password",
            "confirm_password": "student-test-password",
        },
        follow_redirects=True,
    )


def sign_in(client, email, password, next_url=None, path="/login"):
    response = client.get(path, query_string={"next": next_url} if next_url else None)
    return client.post(
        "/login",
        data={"csrf_token": csrf_token(response), "email": email, "password": password},
        follow_redirects=False,
    )


def test_registration_login_authorization_and_logout(client):
    response = register_student(client)
    assert b"Account created!" in response.data

    response = client.get("/register")
    duplicate = client.post(
        "/register",
        data={
            "csrf_token": csrf_token(response),
            "name": "Test Student",
            "email": "student@example.test",
            "password": "student-test-password",
            "confirm_password": "student-test-password",
        },
        follow_redirects=True,
    )
    assert b"already exists" in duplicate.data

    login = sign_in(client, "student@example.test", "student-test-password", "//example.org")
    assert login.location.endswith("/dashboard")
    assert client.get("/admin").location.endswith("/dashboard")
    dashboard = client.get("/dashboard")
    assert b"Welcome back," in dashboard.data and b"Test.</em>" in dashboard.data

    logout = client.post("/logout", data={"csrf_token": csrf_token(dashboard)})
    assert logout.status_code == 302
    assert client.get("/dashboard").location.endswith("/login?next=/dashboard")


def test_public_pages_and_resource_filters_use_neutral_previews(client):
    home = client.get("/")
    assert home.status_code == 200
    assert b"hero-image-placeholder" in home.data
    assert b'class="hero-image"' not in home.data

    directory = client.get("/resources", query_string={"q": "Innovation", "category": "Laboratory"})
    assert directory.status_code == 200
    assert b"Innovation Lab" in directory.data
    assert b"Seminar Hall 02" not in directory.data
    assert b"photo-placeholder" in directory.data

    health = client.get("/health")
    assert health.json == {"status": "ok", "project": "Campus Resource Hub"}
    availability = client.get(
        "/api/availability",
        query_string={"resource_id": 1, "date": (date.today() + timedelta(days=1)).isoformat()},
    )
    assert availability.json == {
        "date": (date.today() + timedelta(days=1)).isoformat(),
        "resource_id": 1,
        "taken": [],
    }
    assert client.get("/api/availability", query_string={"resource_id": 1, "date": "bad"}).status_code == 400


def test_booking_validation_conflicts_same_day_and_cancellation(client, monkeypatch):
    register_student(client)
    sign_in(client, "student@example.test", "student-test-password")

    class MorningClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls.combine(date.today(), datetime.min.time()).replace(hour=8, minute=30)

    monkeypatch.setattr(campus_app, "datetime", MorningClock)
    response = client.get("/resources")
    token = csrf_token(response)
    booking = {
        "csrf_token": token,
        "resource_id": "1",
        "booking_date": date.today().isoformat(),
        "time_slot": "09:00–11:00",
        "purpose": "Project meeting",
    }
    success = client.post("/book", data=booking, follow_redirects=True)
    assert b"Booking request submitted" in success.data
    availability = client.get(
        "/api/availability",
        query_string={"resource_id": 1, "date": date.today().isoformat()},
    )
    assert "09:00–11:00" in availability.json["taken"]

    duplicate = client.post("/book", data=booking, follow_redirects=True)
    assert b"already has a pending or approved request" in duplicate.data

    past_date = {**booking, "booking_date": (date.today() - timedelta(days=1)).isoformat()}
    invalid_date = client.post("/book", data=past_date, follow_redirects=True)
    assert b"future date" in invalid_date.data

    class AfternoonClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls.combine(date.today(), datetime.min.time()).replace(hour=12, minute=30)

    monkeypatch.setattr(campus_app, "datetime", AfternoonClock)
    past_slot = {**booking, "time_slot": "11:00–13:00"}
    invalid_slot = client.post("/book", data=past_slot, follow_redirects=True)
    assert b"future time slot" in invalid_slot.data

    with campus_app.app.app_context():
        booking_id = campus_app.get_db().execute("SELECT id FROM bookings").fetchone()["id"]
    dashboard = client.get("/dashboard")
    cancelled = client.post(
        f"/booking/{booking_id}/cancel",
        data={"csrf_token": csrf_token(dashboard)},
        follow_redirects=True,
    )
    assert b"Booking cancelled." in cancelled.data
    with campus_app.app.app_context():
        status = campus_app.get_db().execute(
            "SELECT status FROM bookings WHERE id=?", (booking_id,)
        ).fetchone()["status"]
    assert status == "Cancelled"


def test_admin_review_resource_management_and_csrf(client):
    register_student(client)
    sign_in(client, "student@example.test", "student-test-password")
    response = client.get("/resources")
    student_token = csrf_token(response)
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    first = client.post(
        "/book",
        data={
            "csrf_token": student_token,
            "resource_id": "1",
            "booking_date": tomorrow,
            "time_slot": "09:00–11:00",
            "purpose": "First request",
        },
    )
    assert first.status_code == 302
    second = client.post(
        "/book",
        data={
            "csrf_token": student_token,
            "resource_id": "1",
            "booking_date": tomorrow,
            "time_slot": "11:00–13:00",
            "purpose": "Second request",
        },
    )
    assert second.status_code == 302
    third = client.post(
        "/book",
        data={
            "csrf_token": student_token,
            "resource_id": "1",
            "booking_date": tomorrow,
            "time_slot": "13:00–15:00",
            "purpose": "Third request",
        },
    )
    assert third.status_code == 302
    with campus_app.app.app_context():
        booking_ids = [
            row["id"] for row in campus_app.get_db().execute(
                "SELECT id FROM bookings ORDER BY id"
            ).fetchall()
        ]

    admin = campus_app.app.test_client()
    login = sign_in(admin, "admin@example.test", "admin-test-password", path="/admin/login")
    assert login.location.endswith("/admin")
    admin_page = admin.get("/admin")
    admin_token = csrf_token(admin_page)
    approved_filter = admin.get("/admin", query_string={"status": "Approved"})
    assert b"First request" not in approved_filter.data

    missing_token = admin.post(f"/admin/booking/{booking_ids[0]}/approve")
    assert missing_token.status_code == 400

    approved = admin.post(
        f"/admin/booking/{booking_ids[0]}/approve",
        data={"csrf_token": admin_token},
        follow_redirects=True,
    )
    assert b"Booking approved." in approved.data
    approved_filter = admin.get("/admin", query_string={"status": "Approved"})
    assert b"First request" in approved_filter.data
    assert b"Second request" not in approved_filter.data
    repeated = admin.post(
        f"/admin/booking/{booking_ids[0]}/approve",
        data={"csrf_token": admin_token},
        follow_redirects=True,
    )
    assert b"Only pending bookings can be reviewed." in repeated.data

    with campus_app.app.app_context():
        campus_app.get_db().execute(
            "UPDATE bookings SET time_slot='09:00–11:00' WHERE id=?", (booking_ids[1],)
        )
        campus_app.get_db().commit()
    conflict = admin.post(
        f"/admin/booking/{booking_ids[1]}/approve",
        data={"csrf_token": admin_token},
        follow_redirects=True,
    )
    assert b"already approved" in conflict.data

    cancelled = admin.post(
        f"/admin/booking/{booking_ids[0]}/cancel",
        data={"csrf_token": admin_token},
        follow_redirects=True,
    )
    assert b"Booking cancelled." in cancelled.data
    cancelled_again = admin.post(
        f"/admin/booking/{booking_ids[0]}/cancel",
        data={"csrf_token": admin_token},
        follow_redirects=True,
    )
    assert b"Only approved bookings can be cancelled" in cancelled_again.data

    second_approved = admin.post(
        f"/admin/booking/{booking_ids[1]}/approve",
        data={"csrf_token": admin_token},
        follow_redirects=True,
    )
    assert b"Booking approved." in second_approved.data
    rejected = admin.post(
        f"/admin/booking/{booking_ids[2]}/reject",
        data={"csrf_token": admin_token},
        follow_redirects=True,
    )
    assert b"Booking rejected." in rejected.data
    rejected_filter = admin.get("/admin", query_string={"status": "Rejected"})
    assert b"Third request" in rejected_filter.data
    assert b"Second request" not in rejected_filter.data

    added = admin.post(
        "/admin/resource",
        data={
            "csrf_token": admin_token,
            "name": "Test Studio",
            "category": "Classroom",
            "location": "Block D",
            "capacity": "20",
            "description": "A test resource",
            "icon": "✳",
        },
        follow_redirects=True,
    )
    assert b"Resource added." in added.data
    with campus_app.app.app_context():
        resource_id = campus_app.get_db().execute(
            "SELECT id FROM resources WHERE name='Test Studio'"
        ).fetchone()["id"]
    toggled = admin.post(
        f"/admin/resource/{resource_id}/toggle",
        data={"csrf_token": admin_token},
        follow_redirects=True,
    )
    assert b"Resource availability updated." in toggled.data

    student_denied = client.post(
        f"/admin/booking/{booking_ids[1]}/reject",
        data={"csrf_token": student_token},
    )
    assert student_denied.status_code == 302
    assert student_denied.location.endswith("/dashboard")

    denied_admin_login = campus_app.app.test_client()
    login_page = denied_admin_login.get("/admin/login")
    denied_student = denied_admin_login.post(
        "/admin/login",
        data={
            "csrf_token": csrf_token(login_page),
            "email": "student@example.test",
            "password": "student-test-password",
        },
        follow_redirects=True,
    )
    assert b"Email or password is incorrect." in denied_student.data
