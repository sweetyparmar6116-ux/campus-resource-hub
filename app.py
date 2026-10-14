"""Campus Resource Hub - a small Flask + SQLite college resource booking app.

Two separate role-based areas:
  * Students  -> /dashboard  (browse resources, request / cancel their own bookings)
  * Admins    -> /admin      (review requests, manage resources and bookings)

The role is always read from the database row of the signed-in account on the
server. It is never taken from a form field or from the browser.
"""
import hmac
import os
import re
import secrets
import sqlite3
from datetime import date, timedelta
from functools import wraps
from urllib.parse import urlparse

from flask import (Flask, abort, flash, g, jsonify, redirect, render_template,
                   request, session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

BASE_DIR = os.path.abspath(os.path.dirname(__file__))


def load_env_file(path):
    """Read KEY=VALUE lines from a .env file (no extra package needed).
    Real environment variables always win over the file."""
    if not os.path.isfile(path):
        return
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_env_file(os.path.join(BASE_DIR, ".env"))

# ---------------------------------------------------------------- configuration
APP_ENV = os.environ.get("APP_ENV", "development").strip().lower()
IS_PRODUCTION = APP_ENV == "production"
DATABASE = os.path.join(BASE_DIR, os.environ.get("DATABASE_PATH", "campushub.db"))

# Development-only admin login. Production refuses to start without real values.
DEV_ADMIN_EMAIL = "admin@campushub.local"
DEV_ADMIN_PASSWORD = "Admin@12345"
PLACEHOLDER_PREFIXES = ("replace-with", "change-this")

TIME_SLOTS = ["09:00–11:00", "11:00–13:00", "13:00–15:00", "15:00–17:00"]
CATEGORIES = ["Laboratory", "Hall", "Equipment", "Meeting Room", "Classroom"]
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _is_placeholder(value):
    return not value or value.startswith(PLACEHOLDER_PREFIXES)


def load_secret_key():
    key = os.environ.get("SECRET_KEY", "").strip()
    if not _is_placeholder(key):
        return key
    if IS_PRODUCTION:
        raise RuntimeError("Set a real SECRET_KEY environment variable when APP_ENV=production.")
    # Development: create a random key once and keep it, so logins survive restarts.
    path = os.path.join(BASE_DIR, "instance", "secret_key")
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as handle:
            return handle.read().strip()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    key = secrets.token_hex(32)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(key)
    os.chmod(path, 0o600)
    return key


def admin_settings():
    """Return (email, password, is_dev_default_password)."""
    email = os.environ.get("ADMIN_EMAIL", "").strip().lower()
    password = os.environ.get("ADMIN_PASSWORD", "")
    if IS_PRODUCTION:
        if not email or _is_placeholder(password) or len(password) < 10:
            raise RuntimeError("Set ADMIN_EMAIL and a strong ADMIN_PASSWORD (10+ characters) "
                               "when APP_ENV=production.")
        return email, password, False
    return email or DEV_ADMIN_EMAIL, password or DEV_ADMIN_PASSWORD, not password


app = Flask(__name__)
app.config.update(
    SECRET_KEY=load_secret_key(),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=IS_PRODUCTION and os.environ.get("COOKIE_SECURE", "1") != "0",
    PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
    MAX_CONTENT_LENGTH=64 * 1024,
)

RESOURCE_SEED = [
    ("Innovation Lab", "Laboratory", "Block A · Floor 2", 40, "Computer lab with collaborative workstations", "💻"),
    ("Seminar Hall 02", "Hall", "Main Building · Floor 1", 100, "A flexible space for talks, workshops and events", "🎤"),
    ("Projector Kit", "Equipment", "Library · Help Desk", 1, "Portable projector kit for classroom presentations", "📽️"),
    ("Collaboration Room", "Meeting Room", "Block B · Floor 3", 12, "A quiet room for team meetings and project work", "🤝"),
    ("Computer Lab 03", "Laboratory", "Block C · Floor 1", 35, "Computer lab for practicals and project sessions", "🖥️"),
    ("Project Discussion Room", "Meeting Room", "Block A · Floor 3", 8, "Small group room for planning and discussion", "💡"),
]


# --------------------------------------------------------------------- database
def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DATABASE)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(_error=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    """Create tables (only if missing - existing data is never dropped) and seed data."""
    admin_email, admin_password, _ = admin_settings()
    db = sqlite3.connect(DATABASE)
    db.execute("PRAGMA foreign_keys = ON")
    db.executescript("""
    CREATE TABLE IF NOT EXISTS users (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      name TEXT NOT NULL,
      email TEXT NOT NULL UNIQUE,
      password_hash TEXT NOT NULL,
      role TEXT NOT NULL DEFAULT 'student' CHECK(role IN ('student','admin')),
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS resources (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      name TEXT NOT NULL,
      category TEXT NOT NULL,
      location TEXT NOT NULL,
      capacity INTEGER NOT NULL DEFAULT 1,
      description TEXT NOT NULL DEFAULT '',
      icon TEXT NOT NULL DEFAULT '✳',
      active INTEGER NOT NULL DEFAULT 1
    );
    CREATE TABLE IF NOT EXISTS bookings (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      resource_id INTEGER NOT NULL REFERENCES resources(id),
      booking_date TEXT NOT NULL,
      time_slot TEXT NOT NULL,
      purpose TEXT NOT NULL,
      status TEXT NOT NULL DEFAULT 'Pending' CHECK(status IN ('Pending','Approved','Rejected','Cancelled')),
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX IF NOT EXISTS idx_bookings_resource_date ON bookings(resource_id, booking_date, time_slot);
    """)
    # Database-level guard against two active requests for the same resource/date/slot
    # (protects against two students clicking at the same moment).
    try:
        db.execute("""CREATE UNIQUE INDEX IF NOT EXISTS uniq_active_booking
                      ON bookings(resource_id, booking_date, time_slot)
                      WHERE status IN ('Pending','Approved')""")
    except sqlite3.IntegrityError:
        print("WARNING: existing duplicate active bookings found; unique booking index not created.")
    if db.execute("SELECT COUNT(*) FROM resources").fetchone()[0] == 0:
        db.executemany(
            "INSERT INTO resources(name,category,location,capacity,description,icon) VALUES(?,?,?,?,?,?)",
            RESOURCE_SEED)
    existing = db.execute("SELECT id, role FROM users WHERE email=?", (admin_email,)).fetchone()
    if not existing:
        db.execute("INSERT INTO users(name,email,password_hash,role) VALUES(?,?,?,?)",
                   ("Campus Administrator", admin_email, generate_password_hash(admin_password), "admin"))
    elif existing[1] != "admin":
        print(f"WARNING: {admin_email} already exists as a student account, so no admin was created. "
              "Set a different ADMIN_EMAIL or remove that account.")
    db.commit()
    db.close()


# ------------------------------------------------------------ users and CSRF
def current_user():
    """The signed-in user, read fresh from the database (cached for one request)."""
    if "_user" not in g:
        user = None
        user_id = session.get("user_id")
        if user_id:
            user = get_db().execute("SELECT id,name,email,role FROM users WHERE id=?", (user_id,)).fetchone()
            if user is None:
                session.clear()  # account no longer exists
        g._user = user
    return g._user


def home_for(user):
    return url_for("admin_dashboard") if user["role"] == "admin" else url_for("dashboard")


def safe_next(target):
    """Only allow redirects to a path on this same site (blocks //evil.com and https://evil.com)."""
    if not target or not target.startswith("/") or target.startswith("//") or "\\" in target:
        return None
    parsed = urlparse(target)
    return target if not parsed.scheme and not parsed.netloc else None


def csrf_token():
    if "_csrf" not in session:
        session["_csrf"] = secrets.token_urlsafe(32)
    return session["_csrf"]


app.jinja_env.globals["csrf_token"] = csrf_token


@app.before_request
def csrf_protect():
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        sent = request.form.get("csrf_token", "") or request.headers.get("X-CSRF-Token", "")
        expected = session.get("_csrf", "")
        if not expected or not hmac.compare_digest(sent, expected):
            abort(400, description="csrf")


@app.after_request
def security_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    if session.get("user_id"):  # don't let the back button show private pages after sign-out
        response.headers["Cache-Control"] = "no-store"
    return response


@app.context_processor
def inject_globals():
    return {"current_user": current_user(), "TIME_SLOTS": TIME_SLOTS, "CATEGORIES": CATEGORIES,
            "dev_admin_hint": None if IS_PRODUCTION else admin_settings()[0]}


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user():
            flash("Please sign in to continue.", "info")
            target = request.full_path.rstrip("?") if request.method == "GET" else None
            return redirect(url_for("login", next=target))
        return view(*args, **kwargs)
    return wrapped


def student_required(view):
    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        user = current_user()
        if user["role"] != "student":
            flash("Administrators manage resources and booking requests from the admin dashboard.", "info")
            return redirect(url_for("admin_dashboard"))
        return view(*args, **kwargs)
    return wrapped


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        user = current_user()
        if not user:
            flash("Please sign in with an administrator account.", "info")
            return redirect(url_for("admin_login"))
        if user["role"] != "admin":
            flash("This page is available to administrators only.", "error")
            return redirect(url_for("dashboard"))
        return view(*args, **kwargs)
    return wrapped


# ----------------------------------------------------------------- public pages
@app.route("/intro")
def intro():
    return render_template("loader.html")


@app.route("/")
def home():
    db = get_db()
    resources = db.execute("SELECT * FROM resources WHERE active=1 ORDER BY id LIMIT 6").fetchall()
    counts = {
        "resources": db.execute("SELECT COUNT(*) FROM resources WHERE active=1").fetchone()[0],
        "categories": db.execute("SELECT COUNT(DISTINCT category) FROM resources WHERE active=1").fetchone()[0],
    }
    return render_template("index.html", resources=resources, counts=counts)


@app.route("/health")
def health():
    return jsonify(status="ok", project="Campus Resource Hub")


# -------------------------------------------------------------- authentication
@app.route("/register", methods=["GET", "POST"])
def register():
    user = current_user()
    if user:
        return redirect(home_for(user))
    form = {"name": "", "email": ""}
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")
        form = {"name": name, "email": email}
        if not name or len(name) > 100:
            flash("Enter your name (up to 100 characters).", "error")
        elif not EMAIL_RE.match(email) or len(email) > 254:
            flash("Enter a valid email address.", "error")
        elif len(password) < 8 or len(password) > 128:
            flash("Choose a password with 8 to 128 characters.", "error")
        elif password != confirm:
            flash("The passwords do not match.", "error")
        else:
            try:
                db = get_db()
                # The role is fixed to 'student' here - public sign-up can never create an admin.
                db.execute("INSERT INTO users(name,email,password_hash,role) VALUES(?,?,?,'student')",
                           (name, email, generate_password_hash(password)))
                db.commit()
                flash("Account created! Sign in to continue.", "success")
                return redirect(url_for("login"))
            except sqlite3.IntegrityError:
                flash("An account with that email already exists.", "error")
    return render_template("auth.html", mode="register", portal="student", form=form)


def _handle_login(portal):
    user = current_user()
    if user:
        return redirect(home_for(user))
    form = {"name": "", "email": ""}
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        form["email"] = email
        row = get_db().execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        if not row or not check_password_hash(row["password_hash"], password):
            flash("Email or password is incorrect.", "error")
        elif portal == "admin" and row["role"] != "admin":
            flash("That account is not an administrator account. Please use the student sign in.", "error")
        else:
            session.clear()  # new session on login (prevents session fixation)
            session["user_id"] = row["id"]
            session.permanent = True
            flash("Welcome back, " + row["name"].split()[0] + "!", "success")
            if row["role"] == "admin":
                return redirect(url_for("admin_dashboard"))
            return redirect(safe_next(request.args.get("next")) or url_for("dashboard"))
    return render_template("auth.html", mode="login", portal=portal, form=form)


@app.route("/login", methods=["GET", "POST"])
def login():
    return _handle_login("student")


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    return _handle_login("admin")


@app.route("/logout", methods=["POST"])
@login_required
def logout():
    session.clear()
    flash("You have been signed out.", "success")
    return redirect(url_for("home"))


# ------------------------------------------------------------------ student area
@app.route("/dashboard")
@login_required
def dashboard():
    user = current_user()
    if user["role"] == "admin":
        return redirect(url_for("admin_dashboard"))
    db = get_db()
    bookings = db.execute("""
      SELECT b.*, r.name AS resource_name, r.category, r.location
      FROM bookings b JOIN resources r ON r.id=b.resource_id
      WHERE b.user_id=? ORDER BY b.created_at DESC, b.id DESC
    """, (user["id"],)).fetchall()
    stats = {
        "total": len(bookings),
        "pending": sum(1 for b in bookings if b["status"] == "Pending"),
        "approved": sum(1 for b in bookings if b["status"] == "Approved"),
        "resources": db.execute("SELECT COUNT(*) FROM resources WHERE active=1").fetchone()[0],
    }
    return render_template("dashboard.html", bookings=bookings, stats=stats, today=date.today().isoformat())


@app.route("/resources")
def resources_page():
    db = get_db()
    q = request.args.get("q", "").strip()[:100]
    category = request.args.get("category", "All")
    categories = [r["category"] for r in db.execute(
        "SELECT DISTINCT category FROM resources WHERE active=1 ORDER BY category").fetchall()]
    sql = "SELECT * FROM resources WHERE active=1"
    params = []
    if q:
        sql += " AND (name LIKE ? OR location LIKE ? OR description LIKE ?)"
        term = f"%{q}%"
        params += [term, term, term]
    if category != "All":
        sql += " AND category=?"
        params.append(category)
    sql += " ORDER BY name"
    resources = db.execute(sql, params).fetchall()
    return render_template("resources.html", resources=resources, categories=categories, q=q, category=category)


@app.route("/api/availability")
@login_required
def availability():
    """Which time slots are already taken for a resource on a date (no personal data)."""
    resource_id = request.args.get("resource_id", type=int)
    try:
        chosen = date.fromisoformat(request.args.get("date", ""))
    except ValueError:
        return jsonify(error="Invalid date"), 400
    rows = get_db().execute("""
      SELECT time_slot FROM bookings
      WHERE resource_id=? AND booking_date=? AND status IN ('Pending','Approved')
    """, (resource_id, chosen.isoformat())).fetchall()
    return jsonify(taken=[r["time_slot"] for r in rows])


@app.route("/book", methods=["POST"])
@student_required
def book():
    user = current_user()
    db = get_db()
    resource_id = request.form.get("resource_id", type=int)
    booking_date = request.form.get("booking_date", "")
    time_slot = request.form.get("time_slot", "")
    purpose = request.form.get("purpose", "").strip()
    try:
        chosen_date = date.fromisoformat(booking_date)
    except (ValueError, TypeError):
        chosen_date = None
    resource = db.execute("SELECT id FROM resources WHERE id=? AND active=1", (resource_id,)).fetchone()
    if (not resource or not chosen_date or chosen_date < date.today()
            or time_slot not in TIME_SLOTS or not purpose or len(purpose) > 300):
        flash("Check the resource, a future date, a time slot, and a purpose (up to 300 characters), then try again.", "error")
        return redirect(url_for("resources_page"))
    conflict = db.execute("""
      SELECT id FROM bookings WHERE resource_id=? AND booking_date=? AND time_slot=?
      AND status IN ('Pending','Approved')
    """, (resource_id, chosen_date.isoformat(), time_slot)).fetchone()
    if conflict:
        flash("That resource already has a pending or approved request for this date and time slot.", "error")
        return redirect(url_for("resources_page"))
    try:
        db.execute("INSERT INTO bookings(user_id,resource_id,booking_date,time_slot,purpose,status) "
                   "VALUES(?,?,?,?,?,'Pending')", (user["id"], resource_id, chosen_date.isoformat(), time_slot, purpose))
        db.commit()
    except sqlite3.IntegrityError:  # someone else grabbed the slot a moment ago
        flash("That slot was just taken by another request. Please choose a different slot.", "error")
        return redirect(url_for("resources_page"))
    flash("Booking request submitted. It is now awaiting administrator review.", "success")
    return redirect(url_for("dashboard"))


@app.route("/booking/<int:booking_id>/cancel", methods=["POST"])
@student_required
def cancel_booking(booking_id):
    user = current_user()
    db = get_db()
    # Students can only ever load their OWN bookings (user_id is part of the query).
    booking = db.execute("SELECT * FROM bookings WHERE id=? AND user_id=?", (booking_id, user["id"])).fetchone()
    if not booking:
        flash("Booking not found.", "error")
    elif booking["status"] in ("Rejected", "Cancelled"):
        flash("This booking can no longer be cancelled.", "info")
    elif booking["booking_date"] < date.today().isoformat():
        flash("Past bookings can't be cancelled.", "info")
    else:
        db.execute("UPDATE bookings SET status='Cancelled' WHERE id=?", (booking_id,))
        db.commit()
        flash("Booking cancelled.", "success")
    return redirect(url_for("dashboard"))


# -------------------------------------------------------------------- admin area
@app.route("/admin")
@admin_required
def admin_dashboard():
    db = get_db()
    status_filter = request.args.get("status", "All")
    statuses = ("Pending", "Approved", "Rejected", "Cancelled")
    sql = """
      SELECT b.*, r.name AS resource_name, u.name AS student_name, u.email AS student_email
      FROM bookings b JOIN resources r ON r.id=b.resource_id JOIN users u ON u.id=b.user_id
    """
    params = []
    if status_filter in statuses:
        sql += " WHERE b.status=?"
        params.append(status_filter)
    else:
        status_filter = "All"
    sql += " ORDER BY CASE b.status WHEN 'Pending' THEN 0 ELSE 1 END, b.created_at DESC, b.id DESC"
    bookings = db.execute(sql, params).fetchall()
    resources = db.execute("SELECT * FROM resources ORDER BY active DESC, name").fetchall()
    by_status = {s: 0 for s in statuses}
    for row in db.execute("SELECT status, COUNT(*) AS n FROM bookings GROUP BY status").fetchall():
        by_status[row["status"]] = row["n"]
    stats = {
        "resources": db.execute("SELECT COUNT(*) FROM resources WHERE active=1").fetchone()[0],
        "pending": by_status["Pending"],
        "users": db.execute("SELECT COUNT(*) FROM users WHERE role='student'").fetchone()[0],
        "bookings": sum(by_status.values()),
        "by_status": by_status,
    }
    return render_template("admin.html", resources=resources, bookings=bookings, stats=stats,
                           status_filter=status_filter, statuses=statuses)


# action -> (new status, statuses it may be applied to)
ADMIN_ACTIONS = {
    "approve": ("Approved", ("Pending",)),
    "reject": ("Rejected", ("Pending",)),
    "cancel": ("Cancelled", ("Pending", "Approved")),
}


@app.route("/admin/booking/<int:booking_id>/<action>", methods=["POST"])
@admin_required
def admin_booking_action(booking_id, action):
    if action not in ADMIN_ACTIONS:
        flash("Unknown booking action.", "error")
        return redirect(url_for("admin_dashboard"))
    new_status, allowed_from = ADMIN_ACTIONS[action]
    db = get_db()
    booking = db.execute("SELECT * FROM bookings WHERE id=?", (booking_id,)).fetchone()
    if not booking:
        flash("Booking not found.", "error")
    elif booking["status"] not in allowed_from:
        flash(f"This booking is already {booking['status'].lower()}, so it can't be changed that way.", "error")
    else:
        if action == "approve":
            conflict = db.execute("""
              SELECT id FROM bookings WHERE resource_id=? AND booking_date=? AND time_slot=?
              AND status='Approved' AND id<>?
            """, (booking["resource_id"], booking["booking_date"], booking["time_slot"], booking_id)).fetchone()
            if conflict:
                flash("Another booking for this resource and slot is already approved. Reject this request instead.", "error")
                return redirect(url_for("admin_dashboard"))
        db.execute("UPDATE bookings SET status=? WHERE id=?", (new_status, booking_id))
        db.commit()
        flash("Booking " + new_status.lower() + ".", "success")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/resource", methods=["POST"])
@admin_required
def add_resource():
    name = request.form.get("name", "").strip()
    category = request.form.get("category", "").strip()
    location = request.form.get("location", "").strip()
    capacity = request.form.get("capacity", type=int)
    description = request.form.get("description", "").strip()
    icon = request.form.get("icon", "").strip()[:8] or "✳"
    if (not name or len(name) > 100 or category not in CATEGORIES or not location or len(location) > 120
            or not capacity or not 1 <= capacity <= 10000 or len(description) > 300):
        flash("Complete all resource fields with a valid category and a capacity from 1 to 10000.", "error")
    else:
        db = get_db()
        db.execute("INSERT INTO resources(name,category,location,capacity,description,icon) VALUES(?,?,?,?,?,?)",
                   (name, category, location, capacity, description, icon))
        db.commit()
        flash("Resource added.", "success")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/resource/<int:resource_id>/toggle", methods=["POST"])
@admin_required
def toggle_resource(resource_id):
    db = get_db()
    resource = db.execute("SELECT * FROM resources WHERE id=?", (resource_id,)).fetchone()
    if resource:
        db.execute("UPDATE resources SET active=? WHERE id=?", (0 if resource["active"] else 1, resource_id))
        db.commit()
        flash("Resource availability updated.", "success")
    else:
        flash("Resource not found.", "error")
    return redirect(url_for("admin_dashboard"))


# ---------------------------------------------------------------- error pages
def _error(code, message):
    return render_template("error.html", code=code, message=message), code


@app.errorhandler(400)
def bad_request(error):
    if getattr(error, "description", "") == "csrf":
        return _error(400, "Your form expired or could not be verified. Go back, refresh the page and try again.")
    return _error(400, "That request could not be understood.")


@app.errorhandler(403)
def forbidden(_error_obj):
    return _error(403, "You don't have permission to view that page.")


@app.errorhandler(404)
def not_found(_error_obj):
    return _error(404, "We couldn't find that page.")


@app.errorhandler(405)
def method_not_allowed(_error_obj):
    return _error(405, "That page can't be opened this way. Use the buttons on the site instead.")


@app.errorhandler(413)
def too_large(_error_obj):
    return _error(413, "That submission was too large.")


@app.errorhandler(500)
def server_error(_error_obj):
    return _error(500, "Something went wrong. Please try again.")


init_db()  # safe to run every start: only creates what is missing

if __name__ == "__main__":
    _email, _password, _using_default = admin_settings()
    if _using_default:
        print(f"DEVELOPMENT ONLY: admin login is {_email} with the default password from the README. "
              "Set ADMIN_EMAIL / ADMIN_PASSWORD (see .env.example) before sharing this app.")
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)),
            debug=(os.environ.get("FLASK_DEBUG") == "1" and not IS_PRODUCTION))
