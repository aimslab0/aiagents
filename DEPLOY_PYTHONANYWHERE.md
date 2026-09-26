# PythonAnywhere deployment

This prepares the existing synchronous, single-user app. No workers or database
changes are needed. The dashboard has no authentication: restrict access using
hosting access controls before exposing private chats or paid research actions.

## 1. Install in a Bash console

Replace the repository URL and `YOUR_USERNAME` throughout this guide. Use a Python
version supported by Django 5.2 and available for both your Web app and virtualenv.

```bash
git clone YOUR_REPOSITORY_URL ~/multi-ai-research
cd ~/multi-ai-research
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
chmod 600 .env
```

If transferring an existing project instead, exclude your local virtualenv and
create a new one on the host. Keep migrations in version control. Never commit
`.env`, the SQLite database, or collected static files. This local workspace is
not currently a Git repository; publish only reviewed source files to your chosen
repository before using the clone/update commands.

## 2. Configure the private .env

```dotenv
SECRET_KEY=REPLACE_WITH_A_LONG_RANDOM_PRODUCTION_SECRET
DEBUG=False
ALLOWED_HOSTS=YOUR_USERNAME.pythonanywhere.com
CSRF_TRUSTED_ORIGINS=https://YOUR_USERNAME.pythonanywhere.com
OPENROUTER_API_KEY=YOUR_PRIVATE_OPENROUTER_KEY
CONSENSUS_API_KEY=YOUR_PRIVATE_CONSENSUS_KEY
```

Generate a new production secret privately with
`python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"`.
Put it in single quotes in `.env`. Do not share the generated value or commit it.
Preserve the remaining research/model settings from `.env.example` or your existing
configuration. These deployment instructions do not select models or enable retries.

Hosts are comma-separated hostnames without schemes or paths. Trusted origins are
comma-separated full HTTPS origins, without trailing slashes. Add a custom domain
to both lists when using one; do not use a wildcard host.

The project loads only `BASE_DIR / '.env'`, independent of the current working
directory. Existing process environment variables take precedence. Variable
interpolation is disabled so `${...}` inside credentials is literal. A missing
`SECRET_KEY` prevents startup. `DEBUG` defaults to False; explicitly use False on
the host. SQLite remains at `BASE_DIR / 'db.sqlite3'`; the app user must be able to
write the database and its parent directory. Keep private backups outside staticfiles.

## 3. Prepare the application

```bash
python manage.py check
python manage.py check --deploy
python manage.py migrate
python manage.py migrate --check
python manage.py collectstatic --noinput
```

`STATIC_URL=/static/`, `STATIC_ROOT=BASE_DIR / 'staticfiles'`; the source `static/`
directory remains in `STATICFILES_DIRS`. These commands do not call research APIs.
Requirements contain Django, python-dotenv, requests and jsonschema; PythonAnywhere
provides the WSGI server, so no extra server package is required.

## 4. Configure the Web tab

Create a **Manual configuration** Python web app, selecting the same Python version
as the virtualenv. Set its virtualenv path to
`/home/YOUR_USERNAME/multi-ai-research/.venv` and source directory to
`/home/YOUR_USERNAME/multi-ai-research`.

Edit the **PythonAnywhere Web tab WSGI configuration file** to contain:

```python
import os
import sys

project_path = '/home/YOUR_USERNAME/multi-ai-research'
if project_path not in sys.path:
    sys.path.insert(0, project_path)
os.environ['DJANGO_SETTINGS_MODULE'] = 'research_ai.settings'
os.environ['DEBUG'] = 'False'

from django.core.wsgi import get_wsgi_application
application = get_wsgi_application()
```

Leave the project's `research_ai/wsgi.py` unchanged. Settings load `.env` before
initializing the application. Do not paste API keys into the WSGI file.

Add exactly this static mapping:

| URL | Directory |
| --- | --- |
| `/static/` | `/home/YOUR_USERNAME/multi-ai-research/staticfiles` |

Never map the project root, `.env`, or database directory as public static content.
Enable HTTPS and the Web tab **Force HTTPS** option, then **Reload**. Visit the HTTPS
dashboard and verify static assets and a CSRF-protected chat action. No paid research
request is needed to check deployment. Ensure your hosting plan permits outbound
HTTPS to `openrouter.ai` and `api.consensus.app` before research use.
Production Balanced Plan-and-Solve also needs outbound HTTPS to `api.semanticscholar.org`.
Its optional key is `SEMANTIC_SCHOLAR_API_KEY`; keep it private in `.env`.

## 5. Checks, logs and local development

`check --deploy` reports `security.W004` (HSTS is not configured) and `security.W008`
(Django SSL redirect is off). HTTPS redirection is delegated to the host's Force
HTTPS setting; verify it on the live site. HSTS is intentionally not enabled before
the final domain and HTTPS configuration are verified. These warnings are not
silenced. Do not blindly trust proxy headers or enable local HTTPS redirects.
Other warnings, including DEBUG, insecure cookies, or a weak secret, must be resolved
for the production environment.

With DEBUG=False, Django's generic error pages hide debug details, cookies require
HTTPS, and console errors go to the host's error log. Application stage/model/error
category logs remain available. Configured credentials are redacted; production
tracebacks retain exception types and function/line locations without exception
payloads, source text or local variables. Framework HTTP messages omit raw URLs.
PythonAnywhere's separate access logs are host-managed: never put credentials in URLs.

For local HTTP development, set `DEBUG=True` and
`ALLOWED_HOSTS=localhost,127.0.0.1,[::1]` in your local `.env`, or override them in
PowerShell for the current process:

```powershell
$env:DEBUG="True"
$env:ALLOWED_HOSTS="localhost,127.0.0.1,[::1]"
.\.venv\Scripts\python.exe manage.py runserver
```

Production requests still run synchronously. Host time limits can interrupt a long
research run; existing saved attempts and recovery tools remain unchanged.

## 6. Update workflow

Back up SQLite consistently while writes are paused (or use SQLite's backup API),
and keep the backup private. In a Bash console:

```bash
cd ~/multi-ai-research
source .venv/bin/activate
git pull --ff-only
python -m pip install -r requirements.txt
python manage.py check
python manage.py check --deploy
python manage.py migrate
python manage.py collectstatic --noinput
```

Reload from the Web tab, inspect the error log, and check the dashboard. `.env` and
the database are local deployment files and must not be replaced by Git updates.

References: [PythonAnywhere Django deployment](https://help.pythonanywhere.com/pages/DeployExistingDjangoProject/),
[PythonAnywhere HTTPS](https://help.pythonanywhere.com/pages/ForcingHTTPS/),
[Django deployment checklist](https://docs.djangoproject.com/en/5.2/howto/deployment/checklist/).
