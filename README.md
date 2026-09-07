# AirMax — air quality PoC

A real-time view of Belgian air quality aggregated by city, ingested from an OpenAQ SNS→SQS stream into PostgreSQL on an 
RDS instance with the PostGIS extension, and displayed by a Django web application.

## Setup

Requires [uv](https://docs.astral.sh/uv/), Node 20+ and Python 3.14.

#### PostGIS

In order to work with geospatial data on the Django app, you'll need to install some libraries on your system. See 
[Installing PostGIS](https://docs.djangoproject.com/en/6.1/ref/contrib/gis/install/postgis/) for all the details. The 
commands below assume that you're on macOS so you can use Homebrew to install them all.

```bash
brew install gdal geos                   # GeoDjango's C libraries
uv sync
npm install
cp .env.example .env                     # fill in credentials
uv run python manage.py migrate
uv run python manage.py createsuperuser
```

The database is a publicly accessible RDS PostgreSQL instance with user and password credentials in `.env`. The first
migration enables PostGIS, so the db user in `.env` has to be a master user with superuser privileges.

## Running

For development, open two terminals, one for Vite serving assets with hot reload and another one for the Django server:

```bash
npm run dev                              # terminal 1
uv run python manage.py runserver        # terminal 2, runs on localhost:8000
```

Or, for a non-debug build:

```bash
npm run build
DJANGO_DEBUG=false uv run python manage.py collectstatic --noinput
DJANGO_DEBUG=false uv run python manage.py runserver  # runs on localhost:8000
```

`DJANGO_VITE["default"]["dev_mode"]` is bound to `DEBUG`, so the two can never drift: with `DEBUG` off, assets are 
resolved through the Vite manifest and served by WhiteNoise, and nothing needs `node` to be alive.

## Frontend

`frontend/` is bundled by [Vite](https://vite.dev/) with [django-vite](https://github.com/MrBin99/django-vite). Tailwind 
and DaisyUI are added for styling, and [Leaflet](https://leafletjs.com/) is the open source mapping library chosen for 
the map. The frontend code was mostly AI generated and has not been reviewed in detail.

## Tests

```bash
uv run pytest
```

Tests build a throwaway database on RDS, so a run takes a few seconds longer than a local one would. Like the frontend
code, they were mostly AI generated and have not been reviewed in detail.

## The ingest measurements Lambda

### The Lambda's db role

The consumer Lambda logs in as `airmax_lambda`, a role with no password that instead authenticates with an IAM token 
minted per invocation, so there is no secret to store or rotate anywhere. To create it, run:

```bash
psql "host=$DB_HOST port=$DB_PORT dbname=$DB_NAME user=$DB_USER sslmode=require" \
     -f scripts/bootstrap_db_user.sql
```

### The function code

`airmax/lambdas/ingest_measurements/` is the Lambda SQS consumer. It parses and upserts one batch of OpenAQ messages in 
a single transaction. It runs on plain psycopg with no Django or other bigger dependencies, which is what keeps the 
deployment zip small.

To set it up, there's a script that does all the work, including building the zip and pushing it to AWS:

```bash
scripts/deploy_lambda.sh              # only updates the function code
scripts/deploy_lambda.sh --enable     # updates and switches the trigger on
scripts/deploy_lambda.sh --disable    # updates and switches it off
```

> [!IMPORTANT]
> To run the scripts above, you'll need to have the [AWS CLI](https://docs.aws.amazon.com/cli/latest/userguide/cli-chap-getting-started.html) 
installed and configured with the right credentials.

## City boundaries

The map displays a set of polygons representing all Belgian municipalitites from 2025. The geospatial city boundaries 
have been extracted from the [Statbel's 2025 municipalities open data](https://statbel.fgov.be/en/open-data/municipalities-2025)
by a python script. The resulting can be found in [data/be_municipalities.geojson](data/be_municipalities.geojson). 
These are the boundaries that will be used to link each ingested measurement to a city. To load it into the 
`airmax_city` table, run:

```bash
uv run python manage.py load_cities
```

### City boundaries on the map view

However, because Statbel's geoJSON is too detailed and the city boundaries change very infrequently, it's not efficient
to query them every time a user loads the map. Instead, a static file is served to the browser with a simplified version
of the boundaries. To update it, run:

```bash
uv run python manage.py build_city_boundaries        # writes static/geo/be_municipalities.geojson
```
