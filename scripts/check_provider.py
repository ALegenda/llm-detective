"""Credential/model preflight. Never prints provider response bodies or secrets."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app import config
from openai import OpenAI, APIStatusError, APIConnectionError
config.preflight()
try:
    c=OpenAI(timeout=20,max_retries=0)
    for name in [config.TEXT_MODEL,config.IMAGE_MODEL]:
        m=c.models.retrieve(name)
        print('Available:',m.id)
except APIStatusError as e:
    print('Provider HTTP status:',e.status_code)
    sys.exit(1)
except APIConnectionError:
    print('Provider connection failed')
    sys.exit(2)
