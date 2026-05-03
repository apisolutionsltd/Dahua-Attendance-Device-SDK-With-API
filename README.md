# Dahua Access Control API

Production-ready REST API wrapping Dahua ASI access controllers (ASI6214S-D, ASI6214S-PW). Built on FastAPI + the Dahua NetSDK.

## Features

- **Users** — list, get, create with face, delete, download face photo
- **Migration** — atomically move users between devices
- **Attendance** — fetch access records by user/date range
- **Background jobs** — long operations (full export, bulk migration) run async with progress tracking
- **JWT auth** — username/password login + bearer tokens (with API key fallback)
- **Rate limiting** — per-IP throttling on auth + write endpoints
- **Structured logging** — JSON logs with request IDs
- **Persistent jobs** — SQLite-backed, survives restarts
- **Dockerized** — single command to run

## Quick start

```powershell
# 1. Setup venv (Windows, 64-bit Python 3.12 recommended)
py -3.12 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
pip install path\to\NetSDK-2.0.0.1-py3-none-win_amd64.whl

# 2. Configure
copy .env.example .env
# Edit .env — set JWT_SECRET, ADMIN_PASSWORD, and DEVICES

# 3. Initialize SQLite DB
python -m scripts.init_db

# 4. Run
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Open http://localhost:8000/docs

## Authentication flow

```bash
# 1. Login → get JWT token
curl -X POST http://localhost:8000/auth/login \
    -H "Content-Type: application/json" \
    -d '{"username":"admin","password":"your-admin-password"}'

# Response: {"access_token":"eyJhbGc...","token_type":"bearer","expires_in":3600}

# 2. Use token on subsequent requests
curl -H "Authorization: Bearer eyJhbGc..." \
    http://localhost:8000/devices/host/users
```

API-key auth still works too for service-to-service traffic — pass `X-API-Key`.

## Endpoint reference

All write endpoints are rate-limited (10/min/IP by default).

### Auth
- `POST /auth/login` — username/password → JWT

### Users (per device)
- `GET    /devices/{name}/users` — list
- `GET    /devices/{name}/users/{user_id}` — detail
- `POST   /devices/{name}/users` — create with face (multipart)
- `DELETE /devices/{name}/users/{user_id}`
- `GET    /devices/{name}/users/{user_id}/face` — JPG bytes

### Migration
- `POST /migrate` — sync, atomic (≤30 s typical)

### Attendance
- `GET /devices/{name}/attendance?days=7&user_id=…`

### Jobs (long operations)
- `POST   /jobs/export/{device}` — schedule a full users+attendance export
- `POST   /jobs/migrate-bulk` — schedule a multi-user migration
- `GET    /jobs/{job_id}` — poll status
- `GET    /jobs` — list recent jobs

### Meta
- `GET /health` — no auth
- `GET /devices` — list configured devices

## Architecture

```
app/
├── main.py                # FastAPI factory, lifespan, middleware
├── config.py              # pydantic-settings
├── logging_setup.py       # JSON logger
├── core/
│   ├── dahua_client.py    # Synchronous SDK wrapper
│   ├── device_pool.py     # Persistent SDK clients + per-device locks
│   ├── security.py        # JWT + password hashing
│   ├── rate_limit.py      # slowapi config
│   └── db.py              # SQLite for jobs
├── schemas/               # Pydantic request/response models
│   ├── user.py
│   ├── attendance.py
│   ├── migration.py
│   ├── auth.py
│   └── job.py
├── services/              # Business logic
│   ├── user_service.py
│   ├── attendance_service.py
│   ├── migration_service.py
│   └── job_service.py
├── routers/
│   ├── auth.py
│   ├── users.py
│   ├── migration.py
│   ├── attendance.py
│   ├── jobs.py
│   └── meta.py
├── exceptions.py
└── dependencies.py        # require_auth, get_pool, etc.

tests/                     # pytest + httpx.AsyncClient
scripts/init_db.py         # one-time DB bootstrap
Dockerfile
docker-compose.yml
```

### Why this design

- **Persistent SDK sessions.** Login is expensive (~1-2s). Pool keeps one logged-in client per device.
- **Per-device locking.** SDK isn't thread-safe per session. Different devices run in parallel; same device serializes.
- **`run_in_executor` for blocking calls.** SDK calls block. Async handlers offload to thread pool to keep event loop responsive.
- **Background jobs for slow ops.** Bulk operations don't block HTTP; client polls `GET /jobs/{id}`.
- **Service layer separation.** Routers handle HTTP only; business logic lives in services and is reusable from background tasks.

## Deployment

```bash
docker-compose up -d
```

Mount the NetSDK wheel and DLLs into the container — see `Dockerfile` for the layout. (The SDK is Windows-only; for Linux you'd need the Linux NetSDK wheel from Dahua.)

## Production checklist

- [ ] Set strong `JWT_SECRET` (32+ chars random)
- [ ] Set strong `ADMIN_PASSWORD`, hash with `python -m scripts.hash_password`
- [ ] Set strong `API_KEY`
- [ ] Restrict `ALLOWED_ORIGINS` to known frontends only
- [ ] Set `DEBUG=false` (hides /docs)
- [ ] Set `LOG_LEVEL=INFO` (or WARNING)
- [ ] Configure HTTPS via reverse proxy (nginx/Caddy)
- [ ] Backup `data/jobs.sqlite` regularly
