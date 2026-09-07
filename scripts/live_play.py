"""Opt-in live acceptance client. Uses only public game API, never the solution DB.
QA_EMAIL/QA_PASSWORD are needed for the first login; local cookie stays in ignored data.
"""
import argparse
import json
import os
import time
import uuid
from pathlib import Path
import httpx

p=argparse.ArgumentParser()
p.add_argument('--url',default='http://127.0.0.1:8080')
p.add_argument('--attempt')
p.add_argument('--text')
p.add_argument('--target',default='')
p.add_argument('--evidence',default='')
p.add_argument('--finish',action='store_true')
p.add_argument('--new-theme')
p.add_argument('--language',default='ru')
p.add_argument('--compact',action='store_true')
args=p.parse_args()
store=Path('data/qa-session.json')
c=httpx.Client(base_url=args.url,headers={'X-Requested-With':'detective'},timeout=20)
if store.exists():c.cookies.update(json.loads(store.read_text()))
if c.get('/api/me').status_code!=200:
    r=c.post('/api/auth/login',json={'email':os.environ['QA_EMAIL'],'password':os.environ['QA_PASSWORD']});r.raise_for_status()
    store.parent.mkdir(exist_ok=True);store.write_text(json.dumps(dict(c.cookies)));store.chmod(0o600)
if args.new_theme:
    r=c.post('/api/cases',json={'theme':args.new_theme,'language':args.language,'difficulty':'medium','duration':'short'},headers={'Idempotency-Key':str(uuid.uuid4())});r.raise_for_status();print(json.dumps(r.json()));raise SystemExit
if not args.attempt:
    print(json.dumps(c.get('/api/cases').json(),ensure_ascii=False,indent=2));raise SystemExit
r=c.get('/api/attempts/'+args.attempt);r.raise_for_status();a=r.json()
if args.text:
    payload={'text':args.text,'target':args.target,'evidence':[e for e in args.evidence.split(',') if e],'version':a['version'],'kind':'finish' if args.finish else 'action','confirmed':args.finish}
    command=c.post('/api/attempts/'+args.attempt+'/commands',json=payload,headers={'Idempotency-Key':str(uuid.uuid4())});command.raise_for_status();cid=command.json()['id']
    print('Command accepted',flush=True)
    end=time.time()+240
    while time.time()<end:
        q=c.get('/api/commands/'+cid).json()
        if q['status'] in ['done','failed']:
            print(json.dumps(q,ensure_ascii=False,indent=2),flush=True);break
        time.sleep(2)
    else:raise RuntimeError('Command still pending; resume from browser')
    a=c.get('/api/attempts/'+args.attempt).json()
w=a['world']
print(json.dumps({'title':a['title'],'status':a['status'],'version':a['version'],'minute':w['minute'],'location':w['location'],'inventory':w['inventory'],'objects':[{k:o[k] for k in ['id','name','open']} for o in w['objects']] if args.compact else w['objects'],'people':w['people'],'evidence':w['evidence'][-4:] if args.compact else w['evidence'],'last_dialogue':w['dialogue'][-2:],'verdict':a.get('verdict')},ensure_ascii=False,indent=2))
