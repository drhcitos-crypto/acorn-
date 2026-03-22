# ACORN V5 — Unified System

Two apps, one database.

## Start ACORN (student portal + admin)
```bash
pip install -r requirements.txt
python app.py
# → http://localhost:5050        (student portal)
# → http://localhost:5050/admin  (admin backend)
```

## Start Northstar (apply portal)
```bash
cd northstar
python app.py
# → http://localhost:5055        (apply + status check)
```

**Run both at the same time** — they share `instance/acorn.db`.

## Admin Login
URL: http://localhost:5050/admin/login
- `admin` / `acorn2025`
- `registrar` / `reg2025`

## What's integrated in V5
- `/admin/admissions` — full list with search, filter, bulk actions
- `/admin/admissions/<id>` — detailed review with essay, all info
- **Approve / Reject / Mark Reviewing** from the ACORN admin panel
- **"Create ACORN Account"** button: one click converts an approved
  application into a real student account with welcome notification
- Northstar `/apply` writes directly into the shared DB
- Northstar `/status` reads application status from the shared DB
