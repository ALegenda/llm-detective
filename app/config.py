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
EVALUATION_REASONING = os.getenv('OPENAI_EVALUATION_REASONING', 'medium')
IMAGE_MODEL = os.getenv('OPENAI_IMAGE_MODEL', 'gpt-image-2.5-flare')
# Explicit sizes/quality keep auto from choosing a more expensive render.
IMAGE_QUALITY = os.getenv('OPENAI_IMAGE_QUALITY', 'low')
IMAGE_FORMAT = os.getenv('OPENAI_IMAGE_FORMAT', 'webp')
IMAGE_COMPRESSION = int(os.getenv('OPENAI_IMAGE_COMPRESSION', '80'))
_custom_image_sizes = IMAGE_MODEL.startswith(('gpt-image-2.', 'gpt-image-2-')) or IMAGE_MODEL == 'gpt-image-2'
IMAGE_SQUARE_SIZE = os.getenv('OPENAI_IMAGE_SQUARE_SIZE', '832x832' if _custom_image_sizes else '1024x1024')
IMAGE_LANDSCAPE_SIZE = os.getenv('OPENAI_IMAGE_LANDSCAPE_SIZE', '1152x768' if _custom_image_sizes else '1536x1024')
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
    if IMAGE_FORMAT not in {'png','webp'} or not 0 <= IMAGE_COMPRESSION <= 100:
        raise RuntimeError('Image output must be PNG or WebP with compression between 0 and 100')
    if IMAGE_QUALITY not in {'low','medium','high','xhigh','max'}:
        raise RuntimeError('An explicit image quality is required')
    for size in (IMAGE_SQUARE_SIZE,IMAGE_LANDSCAPE_SIZE):
        try:
            width,height=map(int,size.split('x'))
            valid=(min(width,height)>0 and max(width,height)<=3840 and width%16==height%16==0
                and 655360<=width*height<=8294400 and max(width,height)<=3*min(width,height))
        except ValueError:valid=False
        if not valid:raise RuntimeError('Unsupported image dimensions')
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
