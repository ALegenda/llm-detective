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
TEXT_MODEL = os.getenv('OPENAI_TEXT_MODEL', 'gpt-6-luna')
STORY_MODEL = os.getenv('OPENAI_STORY_MODEL', 'gpt-6-luna')
STORY_REASONING = os.getenv('OPENAI_STORY_REASONING', 'medium')
TEXT_REASONING = os.getenv('OPENAI_TEXT_REASONING', 'none')
DIALOGUE_REASONING = os.getenv('OPENAI_DIALOGUE_REASONING', 'low')
IMAGE_MODEL = os.getenv('OPENAI_IMAGE_MODEL', 'gpt-image-2.5-flare')
PRODUCTION = os.getenv('APP_ENV') == 'production'
# Render sets RENDER_EXTERNAL_URL (https://….onrender.com); prefer explicit APP_ORIGIN.
ORIGIN = (
    os.getenv('APP_ORIGIN')
    or os.getenv('RENDER_EXTERNAL_URL')
    or 'http://localhost:8080'
).rstrip('/')
ADMINS = {x.strip().lower() for x in os.getenv('ADMIN_EMAILS', '').split(',') if x.strip()}
TELEGRAM_CLIENT_ID = os.getenv('TELEGRAM_CLIENT_ID', '').strip()
TELEGRAM_CLIENT_SECRET = os.getenv('TELEGRAM_CLIENT_SECRET', '').strip()
TELEGRAM_ADMIN_IDS = {x.strip() for x in os.getenv('TELEGRAM_ADMIN_IDS', '').split(',') if x.strip()}
TELEGRAM_ISSUER = 'https://oauth.telegram.org'
SCHEMA_VERSION = 4
PROMPT_VERSION = '2026-09-25.4'
ASSET_STORAGE = os.getenv('ASSET_STORAGE','local')
R2_ENDPOINT_URL = os.getenv('R2_ENDPOINT_URL','')
R2_BUCKET = os.getenv('R2_BUCKET','')
R2_ACCESS_KEY_ID = os.getenv('R2_ACCESS_KEY_ID','')
R2_SECRET_ACCESS_KEY = os.getenv('R2_SECRET_ACCESS_KEY','')
R2_MIGRATE_LOCAL = os.getenv('R2_MIGRATE_LOCAL','0') == '1'
R2_MAX_BYTES = int(os.getenv('R2_MAX_BYTES','9000000000'))


def preflight():
    if ASSET_STORAGE not in {'local','r2'}:
        raise RuntimeError('Unsupported ASSET_STORAGE')
    if ASSET_STORAGE=='r2' and (not all((R2_ENDPOINT_URL,R2_BUCKET,R2_ACCESS_KEY_ID,R2_SECRET_ACCESS_KEY)) or not R2_ENDPOINT_URL.startswith('https://')):
        raise RuntimeError('Complete HTTPS R2 configuration is required')
    if not os.getenv('OPENAI_API_KEY'):
        raise RuntimeError('OPENAI_API_KEY is required. No demo fallback is provided.')
    if bool(TELEGRAM_CLIENT_ID) != bool(TELEGRAM_CLIENT_SECRET):
        raise RuntimeError('TELEGRAM_CLIENT_ID and TELEGRAM_CLIENT_SECRET must be configured together.')
    if PRODUCTION and not ORIGIN.startswith('https://'):
        raise RuntimeError('Production requires an HTTPS APP_ORIGIN (or RENDER_EXTERNAL_URL).')
    DATA.mkdir(parents=True, exist_ok=True)
    (DATA / 'assets').mkdir(exist_ok=True)
