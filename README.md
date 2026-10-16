# Campus Resource Hub

A Flask + SQLite college resource booking prototype with a botanical green/cream UI, animated loading screen, resource carousel, student accounts, and an administrator dashboard.

## Features

- Animated welcome/loading screen and responsive green botanical-inspired interface
- Student registration and login with hashed passwords
- Separate student and admin dashboards
- Searchable/filterable campus resource directory
- Booking requests with future-date/time-slot validation
- Prevents duplicate pending/approved requests for the same resource, date, and slot
- Students can view and cancel their bookings
- Admin can approve/reject requests and add/deactivate resources
- `/health` endpoint for a simple health check

## Run in GitHub Codespaces

Open the repository in Codespaces and run:

```bash
python -m pip install -r requirements.txt
python app.py
```

Open the **Ports** tab, forward port `5000` if needed, and choose **Open in Browser**.

## Demo accounts

The application seeds an administrator account on first run:

- Email: `admin@campushub.local`
- Password: `Admin@12345`

**Change these credentials before sharing or deploying the application.** You can set `ADMIN_EMAIL`, `ADMIN_PASSWORD`, and `SECRET_KEY` as environment variables before the first run. The seeded admin account is created only if that email does not already exist in the database.

Students can register using the Register link. Admin registration is intentionally not public.

## Local development

Python 3.10+ recommended.

```bash
python -m venv .venv
# Windows PowerShell: .\.venv\Scripts\Activate.ps1
# macOS/Linux/Codespaces: source .venv/bin/activate
python -m pip install -r requirements.txt
python app.py
```

## Important limitations and security notes

- This is an academic prototype, not production-hardened software.
- SQLite is used for local development. For a cloud deployment, migrate to a managed database such as Amazon RDS.
- The app uses environment variables for the secret key and admin bootstrap credentials. If `SECRET_KEY` is not set, a random key is generated at startup; set a stable secret for deployments that restart or run multiple workers. Never commit `.env` or cloud credentials.
- Forms include session-bound CSRF protection. Add rate limiting, password reset, email verification, stronger production session/security settings, and production WSGI hosting before real-world use.
- The app uses a small set of sample resources. Update them through the admin dashboard.
- Resource previews use neutral local placeholders; no FCRIT facility photographs have been added because official provenance and reuse permissions have not been verified.
- Booking requests are stored persistently in SQLite; the database file is created on first launch.
- The animated intro can be skipped with the Skip link and respects reduced-motion preferences.
- AWS resources may incur charges. Check current pricing and free-tier eligibility before provisioning anything.
