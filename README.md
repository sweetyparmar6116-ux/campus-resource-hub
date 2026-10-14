# Campus Resource Hub

A Flask + SQLite college resource booking prototype with a botanical green/cream UI, an animated welcome screen, a resource carousel, student accounts, and a separate administrator dashboard. Built as a Cloud Computing Lab (CCL) mini-project.

## Features

- Animated welcome screen (shown once per browser session, skippable, respects reduced-motion)
- **Two separate role-based areas**
  - **Student** (`/dashboard`): browse resources, see which time slots are free, request bookings, view and cancel your own bookings
  - **Admin** (`/admin`): statistics, approve / reject / cancel booking requests, filter by status, add or hide resources
- Student registration and login with hashed passwords; a separate **Administrator sign in** page (`/admin/login`)
- Role checks are enforced on the server for every protected route, using the role stored in the database (never a form field)
- CSRF protection on every form, safe redirects, session cookies marked HttpOnly / SameSite
- Duplicate pending/approved requests for the same resource, date and slot are prevented (in the app and in the database)
- `/health` endpoint for a simple health check

## Run in GitHub Codespaces

```bash
python -m pip install -r requirements.txt
python app.py
```

Open the **Ports** tab, forward port `5000` if needed, and choose **Open in Browser**.

## Demo accounts (development only)

| Role | Email | Password |
|------|-------|----------|
| Administrator | `admin@campushub.local` | `Admin@12345` |
| Student | create one on the **Register** page, e.g. `student@example.com` | choose your own (8+ characters) |

The admin account is created **only the first time the database is created**. To use your own admin login, copy `.env.example` to `.env` and set `ADMIN_EMAIL` and `ADMIN_PASSWORD` **before the first run**. If `campushub.db` already exists, changing these values does nothing; delete `campushub.db` (this erases all data) to start fresh.

The default password is for local development only. With `APP_ENV=production` the app refuses to start unless a real `SECRET_KEY`, `ADMIN_EMAIL` and `ADMIN_PASSWORD` are provided.

## How to test both dashboards manually

You can only be signed in as one role per browser at a time, so use **two browsers** (or one normal window and one private window), or sign out between roles.

1. **Student:** open the site, click **Login / Sign up**, register, sign in. You land on the **Student dashboard** (green "Student" badge in the header). Open **Resources**, click **Request booking**, pick a date and a free time slot, submit. The request shows as *Pending* on your dashboard.
2. **Admin:** in the other browser open **Admin sign in** (top navigation, or `/admin/login`) and sign in with the admin account. You land on the **Admin dashboard** (deep-green header, gold "Administrator" badge). Approve or reject the student's request.
3. **Back as the student:** refresh your dashboard; the status is now *Approved* or *Rejected*.
4. **Check protection:** while signed in as the student, visit `/admin`. You are sent back to your dashboard with a message. While signed out, `/dashboard` and `/admin` send you to the sign-in page.

## Automated tests

```bash
python -m pip install -r requirements-dev.txt
python -m pytest
```

The tests use a temporary database and never touch `campushub.db`.

## Project layout

```
app.py               Flask app: routes, roles, database
templates/           HTML pages (base.html is the shared layout)
static/              styles.css and app.js
tests/               automated tests
requirements.txt     runtime dependencies
requirements-dev.txt adds pytest
.env.example         copy to .env for local settings
Dockerfile           production-style container (needs SECRET_KEY / ADMIN_* variables)
```

## Configuration (environment variables or `.env`)

| Variable | Purpose |
|----------|---------|
| `APP_ENV` | `development` (default) or `production` |
| `SECRET_KEY` | Signs login sessions. Development: auto-generated in `instance/secret_key`. Production: required |
| `ADMIN_EMAIL`, `ADMIN_PASSWORD` | First-run admin account. Production: required |
| `DATABASE_PATH` | SQLite file (default `campushub.db`) |
| `PORT`, `FLASK_DEBUG` | Optional. Debug mode is ignored in production |

## Limitations and security notes

- This is an academic prototype, not hardened production software.
- There is **no login rate limiting**, password reset or email verification. Add these before real-world use.
- Pages load fonts from Google Fonts and photos from Unsplash. If these are blocked or offline, the app falls back to system fonts and plain green panels.
- Times use the server's clock. A server in a different time zone may treat "today" differently from your campus.
- SQLite is for local development. For a cloud deployment, move to a managed database and run behind a production WSGI server (the Dockerfile uses gunicorn). Serve over HTTPS.
- Never commit `.env`, `campushub.db` or `instance/`. They are listed in `.gitignore`.
- AWS resources may incur charges. Check current pricing and free-tier eligibility before provisioning anything.
