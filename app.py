import os
import sqlite3
from functools import wraps
from datetime import datetime, date
from flask import Flask, render_template, request, redirect, url_for, session, flash, g, jsonify
from werkzeug.security import generate_password_hash, check_password_hash

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DATABASE = os.path.join(BASE_DIR, os.environ.get("DATABASE_PATH", "campushub.db"))
app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "change-this-development-secret")
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

RESOURCE_SEED = [
    ("Innovation Lab", "Laboratory", "Block A · Floor 2", 40, "Computer lab with collaborative workstations", "💻"),
    ("Seminar Hall 02", "Hall", "Main Building · Floor 1", 100, "A flexible space for talks, workshops and events", "🎤"),
    ("Projector Kit", "Equipment", "Library · Help Desk", 1, "Portable projector kit for classroom presentations", "📽️"),
    ("Collaboration Room", "Meeting Room", "Block B · Floor 3", 12, "A quiet room for team meetings and project work", "🤝"),
    ("Computer Lab 03", "Laboratory", "Block C · Floor 1", 35, "Computer lab for practicals and project sessions", "🖥️"),
    ("Project Discussion Room", "Meeting Room", "Block A · Floor 3", 8, "Small group room for planning and discussion", "💡"),
]

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
    count = db.execute("SELECT COUNT(*) FROM resources").fetchone()[0]
    if count == 0:
        db.executemany(
            "INSERT INTO resources(name,category,location,capacity,description,icon) VALUES(?,?,?,?,?,?)",
            RESOURCE_SEED
        )
    admin_email = os.environ.get("ADMIN_EMAIL", "admin@campushub.local").strip().lower()
    admin_password = os.environ.get("ADMIN_PASSWORD", "Admin@12345")
    existing = db.execute("SELECT id FROM users WHERE email=?", (admin_email,)).fetchone()
    if not existing:
        db.execute("INSERT INTO users(name,email,password_hash,role) VALUES(?,?,?,?)",
                   ("Campus Administrator", admin_email, generate_password_hash(admin_password), "admin"))
    db.commit()
    db.close()

def current_user():
    user_id = session.get("user_id")
    if not user_id:
        return None
    return get_db().execute("SELECT id,name,email,role FROM users WHERE id=?", (user_id,)).fetchone()

@app.context_processor
def inject_user():
    return {"current_user": current_user()}

def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user():
            flash("Please sign in to continue.", "info")
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapped

def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        user = current_user()
        if not user:
            flash("Please sign in with an administrator account.", "info")
            return redirect(url_for("login"))
        if user["role"] != "admin":
            flash("This page is available to administrators only.", "error")
            return redirect(url_for("dashboard"))
        return view(*args, **kwargs)
    return wrapped

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

@app.route("/register", methods=["GET", "POST"])
def register():
    if current_user():
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")
        if not name or not email or len(password) < 8:
            flash("Enter your name, a valid email, and a password with at least 8 characters.", "error")
        elif password != confirm:
            flash("The passwords do not match.", "error")
        else:
            try:
                db = get_db()
                db.execute("INSERT INTO users(name,email,password_hash,role) VALUES(?,?,?,'student')",
                           (name, email, generate_password_hash(password)))
                db.commit()
                flash("Account created! Sign in to continue.", "success")
                return redirect(url_for("login"))
            except sqlite3.IntegrityError:
                flash("An account with that email already exists.", "error")
    return render_template("auth.html", mode="register")

@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user():
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        user = get_db().execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        if user and check_password_hash(user["password_hash"], password):
            session.clear()
            session["user_id"] = user["id"]
            flash("Welcome back, " + user["name"].split()[0] + "!", "success")
            target = request.args.get("next")
            if user["role"] == "admin":
                return redirect(url_for("admin_dashboard"))
            return redirect(target if target and target.startswith("/") else url_for("dashboard"))
        flash("Email or password is incorrect.", "error")
    return render_template("auth.html", mode="login")

@app.route("/logout", methods=["POST"])
@login_required
def logout():
    session.clear()
    flash("You have been signed out.", "success")
    return redirect(url_for("home"))

@app.route("/dashboard")
@login_required
def dashboard():
    db = get_db()
    user = current_user()
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
    if user["role"] == "admin":
        return redirect(url_for("admin_dashboard"))
    return render_template("dashboard.html", bookings=bookings, stats=stats)

@app.route("/resources")
def resources_page():
    db = get_db()
    q = request.args.get("q", "").strip()
    category = request.args.get("category", "All")
    categories = [r["category"] for r in db.execute("SELECT DISTINCT category FROM resources WHERE active=1 ORDER BY category").fetchall()]
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

@app.route("/book", methods=["POST"])
@login_required
def book():
    user = current_user()
    if user["role"] != "student":
        flash("Administrators manage resources and booking requests from the admin panel.", "info")
        return redirect(url_for("admin_dashboard"))
    resource_id = request.form.get("resource_id", type=int)
    booking_date = request.form.get("booking_date", "")
    time_slot = request.form.get("time_slot", "")
    purpose = request.form.get("purpose", "").strip()
    try:
        chosen_date = date.fromisoformat(booking_date)
    except (ValueError, TypeError):
        chosen_date = None
    allowed_slots = {"09:00–11:00", "11:00–13:00", "13:00–15:00", "15:00–17:00"}
    resource = get_db().execute("SELECT id FROM resources WHERE id=? AND active=1", (resource_id,)).fetchone()
    if not resource or not chosen_date or chosen_date < date.today() or time_slot not in allowed_slots or not purpose:
        flash("Check the resource, future date, time slot, and purpose, then try again.", "error")
        return redirect(url_for("resources_page"))
    db = get_db()
    conflict = db.execute("""
      SELECT id FROM bookings WHERE resource_id=? AND booking_date=? AND time_slot=?
      AND status IN ('Pending','Approved')
    """, (resource_id, booking_date, time_slot)).fetchone()
    if conflict:
        flash("That resource already has a pending or approved request for this date and time slot.", "error")
        return redirect(url_for("resources_page"))
    db.execute("INSERT INTO bookings(user_id,resource_id,booking_date,time_slot,purpose,status) VALUES(?,?,?,?,?,'Pending')",
               (user["id"], resource_id, booking_date, time_slot, purpose))
    db.commit()
    flash("Booking request submitted. It is now awaiting administrator review.", "success")
    return redirect(url_for("dashboard"))

@app.route("/booking/<int:booking_id>/cancel", methods=["POST"])
@login_required
def cancel_booking(booking_id):
    user = current_user()
    db = get_db()
    booking = db.execute("SELECT * FROM bookings WHERE id=?", (booking_id,)).fetchone()
    if not booking or (user["role"] != "admin" and booking["user_id"] != user["id"]):
        flash("Booking not found or you don't have permission to cancel it.", "error")
    elif booking["status"] in ("Rejected", "Cancelled"):
        flash("This booking can no longer be cancelled.", "info")
    else:
        db.execute("UPDATE bookings SET status='Cancelled' WHERE id=?", (booking_id,))
        db.commit()
        flash("Booking cancelled.", "success")
    return redirect(url_for("admin_dashboard" if user["role"] == "admin" else "dashboard"))

@app.route("/admin")
@admin_required
def admin_dashboard():
    db = get_db()
    resources = db.execute("SELECT * FROM resources ORDER BY active DESC, name").fetchall()
    bookings = db.execute("""
      SELECT b.*, r.name AS resource_name, u.name AS student_name, u.email AS student_email
      FROM bookings b JOIN resources r ON r.id=b.resource_id JOIN users u ON u.id=b.user_id
      ORDER BY CASE b.status WHEN 'Pending' THEN 0 ELSE 1 END, b.created_at DESC
    """).fetchall()
    stats = {
      "resources": db.execute("SELECT COUNT(*) FROM resources WHERE active=1").fetchone()[0],
      "pending": db.execute("SELECT COUNT(*) FROM bookings WHERE status='Pending'").fetchone()[0],
      "users": db.execute("SELECT COUNT(*) FROM users WHERE role='student'").fetchone()[0],
      "bookings": db.execute("SELECT COUNT(*) FROM bookings").fetchone()[0],
    }
    return render_template("admin.html", resources=resources, bookings=bookings, stats=stats)

@app.route("/admin/booking/<int:booking_id>/<action>", methods=["POST"])
@admin_required
def admin_booking_action(booking_id, action):
    if action not in ("approve", "reject"):
        flash("Unknown booking action.", "error")
        return redirect(url_for("admin_dashboard"))
    db = get_db()
    booking = db.execute("SELECT * FROM bookings WHERE id=?", (booking_id,)).fetchone()
    if not booking or booking["status"] != "Pending":
        flash("Only pending bookings can be reviewed.", "error")
        return redirect(url_for("admin_dashboard"))
    if action == "approve":
        conflict = db.execute("""
          SELECT id FROM bookings WHERE resource_id=? AND booking_date=? AND time_slot=?
          AND status='Approved' AND id<>?
        """, (booking["resource_id"], booking["booking_date"], booking["time_slot"], booking_id)).fetchone()
        if conflict:
            flash("Another booking for this resource and slot is already approved. Reject this request instead.", "error")
            return redirect(url_for("admin_dashboard"))
        db.execute("UPDATE bookings SET status='Approved' WHERE id=?", (booking_id,))
    else:
        db.execute("UPDATE bookings SET status='Rejected' WHERE id=?", (booking_id,))
    db.commit()
    flash("Booking " + ("approved." if action == "approve" else "rejected."), "success")
    return redirect(url_for("admin_dashboard"))

@app.route("/admin/resource", methods=["POST"])
@admin_required
def add_resource():
    name = request.form.get("name", "").strip()
    category = request.form.get("category", "").strip()
    location = request.form.get("location", "").strip()
    capacity = request.form.get("capacity", type=int)
    description = request.form.get("description", "").strip()
    icon = request.form.get("icon", "✳").strip()[:8]
    if not name or category not in ("Laboratory", "Hall", "Equipment", "Meeting Room", "Classroom") or not location or not capacity or capacity < 1:
        flash("Complete all resource fields with a valid category and capacity.", "error")
    else:
        get_db().execute("INSERT INTO resources(name,category,location,capacity,description,icon) VALUES(?,?,?,?,?,?)",
                         (name, category, location, capacity, description, icon or "✳"))
        get_db().commit()
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
    return redirect(url_for("admin_dashboard"))

@app.errorhandler(404)
def not_found(_error):
    return render_template("error.html", code=404, message="We couldn't find that page."), 404

@app.errorhandler(500)
def server_error(_error):
    return render_template("error.html", code=500, message="Something went wrong. Please try again."), 500

if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=os.environ.get("FLASK_DEBUG") == "1")
