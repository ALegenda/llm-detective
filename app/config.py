import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# A caller may explicitly select an existing env file; no secrets are copied.
for path in [Path(os.getenv('ENV_FILE', ROOT / '.env'))]:
    if path.is_file():
        for line in path.read_text().splitlines():
            key, sep, value = line.strip().partition('=')
            if sep and key and not key.startswith('#'):
                os.environ.setdefault(key, value.strip().strip('\"\''))
DATA = Path(os.getenv('DATA_DIR', ROOT / 'data')).resolve()
DB_PATH = DATA / 'detective.sqlite3'
TEXT_MODEL = os.getenv('OPENAI_TEXT_MODEL', 'gpt-5.4-mini')
IMAGE_MODEL = os.getenv('OPENAI_IMAGE_MODEL', 'gpt-image-1.5')
PRODUCTION = os.getenv('APP_ENV') == 'production'
# Render sets RENDER_EXTERNAL_URL (https://….onrender.com); prefer explicit APP_ORIGIN.
ORIGIN = (
    os.getenv('APP_ORIGIN')
    or os.getenv('RENDER_EXTERNAL_URL')
    or 'http://localhost:8080'
).rstrip('/')
ADMINS = {x.strip().lower() for x in os.getenv('ADMIN_EMAILS', '').split(',') if x.strip()}
SCHEMA_VERSION = 2
PROMPT_VERSION = '2026-09-08.3'


def preflight():
    if not os.getenv('OPENAI_API_KEY'):
        raise RuntimeError('OPENAI_API_KEY is required. No demo fallback is provided.')
    if PRODUCTION and not ORIGIN.startswith('https://'):
        raise RuntimeError('Production requires an HTTPS APP_ORIGIN (or RENDER_EXTERNAL_URL).')
    DATA.mkdir(parents=True, exist_ok=True)
    (DATA / 'assets').mkdir(exist_ok=True)
