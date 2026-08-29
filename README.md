# AirMax — air quality PoC

A real-time view of Belgian air quality, ingested from an OpenAQ SNS→SQS stream into
PostgreSQL on RDS and displayed by a Django application behind a login.

## Layout

| | |
|---|---|
| `webappconf/` | Django project — settings, URLs, WSGI |
| `airmax/` | the single application — user model, login form, measurements, aggregation, the map page |
| `templates/` | server-rendered templates |
| `frontend/` | Vite entry point — Tailwind v4 + DaisyUI |
| `static/dist/` | Vite build output, gitignored — rebuild with `npm run build` |
| `tests/` | pytest suite |
| `scripts/` | operational one-offs (SQS capture, RDS pricing, security-group sync) |
| `prototype/` | throwaway feasibility spike; not extended, kept for its findings |

## Setup

Requires [uv](https://docs.astral.sh/uv/) and Node 20+. Python 3.14 and Django 6.1;
uv installs the interpreter itself from `.python-version`.

```bash
uv sync                       # Python dependencies into .venv
npm install                   # front-end toolchain
cp .env.example .env          # then fill in missing credentials
uv run python manage.py migrate
uv run python manage.py createsuperuser
```

The database is the RDS instance; there is no local Postgres. `rds.force_ssl` is on, so
Django connects with `sslmode=require` — a plaintext attempt is refused with
*"no pg_hba.conf entry … no encryption"*, which reads like a bad password but is not.

The instance accepts connections from one `/32` only. If `migrate` or `psql` hangs for
about thirty seconds, the home IP has moved:

```bash
scripts/sync_home_ip.sh
```

## Running

**Development** — two processes, Vite serving assets with hot reload:

```bash
npm run dev                              # terminal 1
uv run python manage.py runserver        # terminal 2
```

**As it runs for a demo** — one process, no Node:

```bash
npm run build
DJANGO_DEBUG=false uv run python manage.py collectstatic --noinput
DJANGO_DEBUG=false uv run python manage.py runserver
```

`DJANGO_VITE["default"]["dev_mode"]` is bound to `DEBUG`, so the two can never drift: with
`DEBUG` off, assets are resolved through the Vite manifest and served by WhiteNoise, and
nothing needs `node` to be alive. Build before presenting.

## Time

Two settings, two jobs. **`USE_TZ = True` owns storage**: every datetime lands in a
`timestamptz` column as UTC whatever offset it arrived on, so aggregation and window bounds are
immune to daylight saving. **`TIME_ZONE = "Europe/Brussels"` owns rendering** — a reading at a
Belgian station is a fact about Belgian air at a Belgian hour, and that is true whoever is
looking, so the zone is fixed rather than taken from the browser.

They are independent: flipping `TIME_ZONE` changes what the page shows and nothing about what
the database holds, which `tests/` asserts in both directions.

One consequence worth knowing. Outside a request there is no active zone, so `TIME_ZONE` is
also what `make_aware()` assumes — in a management command it yields `+02:00` in summer.
**Ingestion must attach UTC from the offset in the payload and never lean on the ambient
zone.**

## Tests

```bash
uv run pytest
```

Tests build a throwaway database on RDS, so a run takes a few seconds longer than a local
one would. They are deliberately few and sit at two seams — the message parser and the
window aggregate.
