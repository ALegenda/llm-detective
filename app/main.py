import asyncio
import contextlib
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import threading
import time
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from . import config, db, world, worker
from .models import Settings, AuthInput, CommandInput, NoteInput


def password_hash(password, salt=None):
    salt=salt or secrets.token_hex(16)
    return salt+':'+hashlib.scrypt(password.encode(),salt=salt.encode(),n=16384,r=8,p=1).hex()


def authenticate(request:Request):
    token=request.cookies.get('detective_session','')
    row=db.one('SELECT users.* FROM users JOIN sessions ON sessions.user_id=users.id WHERE sessions.token=? AND sessions.expires>?',(hashlib.sha256(token.encode()).hexdigest(),time.time()))
    if not row:raise HTTPException(401,'Войдите в аккаунт, чтобы продолжить.')
    return row


def owned_case(cid,user):
    c=db.one('SELECT * FROM cases WHERE id=? AND user_id=?',(cid,user['id']))
    if not c:raise HTTPException(404,'Дело не найдено.')
    return c


def owned_attempt(aid,user):
    a=db.one('SELECT * FROM attempts WHERE id=? AND user_id=?',(aid,user['id']))
    if not a:raise HTTPException(404,'Попытка не найдена.')
    return a


def key(request):
    value=request.headers.get('Idempotency-Key','')
    if not re.fullmatch(r'[a-zA-Z0-9_-]{8,100}',value):raise HTTPException(400,'Нужен ключ запроса.')
    return value


def admin(user=Depends(authenticate)):
    if user['email'] not in config.ADMINS:raise HTTPException(403,'Требуется доступ администратора.')
    return user


@asynccontextmanager
async def lifespan(app):
    config.preflight();db.init()
    stop=threading.Event()
    count=max(2,min(4,int(os.getenv('WORKERS','2'))))
    threads=[threading.Thread(target=worker.run,args=(stop,'interactive' if i==0 else 'assets'),daemon=True) for i in range(count)]
    for t in threads:t.start()
    yield
    stop.set()
    # Finish in-flight jobs on graceful shutdown; crash recovery uses lease expiry.
    await asyncio.gather(*(asyncio.to_thread(t.join,240) for t in threads))


app=FastAPI(title='Версия — detective engine',version='0.1.0',lifespan=lifespan,docs_url=None if config.PRODUCTION else '/docs')


@app.middleware('http')
async def security(request,call_next):
    if request.method in ['POST','PUT','PATCH','DELETE']:
        origin=request.headers.get('origin')
        allowed={config.ORIGIN}
        if not config.PRODUCTION:allowed.update({'http://localhost:8080','http://127.0.0.1:8080'})
        if (origin and origin not in allowed) or request.headers.get('X-Requested-With')!='detective':
            return JSONResponse({'detail':'Недопустимый источник запроса.'},status_code=403)
        if int(request.headers.get('content-length','0') or 0)>20000:
            return JSONResponse({'detail':'Слишком большой запрос.'},status_code=413)
    response=await call_next(request)
    response.headers['X-Content-Type-Options']='nosniff'
    response.headers['Referrer-Policy']='same-origin'
    response.headers['Content-Security-Policy']="default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    if request.url.path.startswith('/api/'):response.headers['Cache-Control']='no-store'
    elif request.url.path=='/' or request.url.path.endswith(('.js','.css','.html')):response.headers['Cache-Control']='no-cache'
    return response


@app.get('/api/health')
def health():
    db.one('SELECT 1')
    return {'status':'ok','version':'0.1.0'}


@app.post('/api/auth/{action}')
def auth(action:str,body:AuthInput,request:Request):
    if action not in ['register','login']:raise HTTPException(404)
    if not db.rate_limit('auth:'+request.client.host,20,300):raise HTTPException(429,'Слишком много попыток. Подождите несколько минут.')
    email=body.email.strip().lower()
    if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',email):raise HTTPException(422,'Проверьте адрес почты.')
    if action=='register':
        try:
            with db.transaction() as con:
                uid=db.uid();con.execute('INSERT INTO users VALUES(?,?,?,?)',(uid,email,password_hash(body.password),time.time()))
        except sqlite3.IntegrityError:raise HTTPException(409,'Не удалось создать аккаунт. Попробуйте войти.')
    else:
        user=db.one('SELECT * FROM users WHERE email=?',(email,))
        salt=user['password'].split(':')[0] if user else '0'*32
        candidate=password_hash(body.password,salt)
        if not user or not hmac.compare_digest(candidate,user['password']):raise HTTPException(401,'Неверная почта или пароль.')
        uid=user['id']
    token=secrets.token_urlsafe(32)
    with db.transaction() as con:
        con.execute('DELETE FROM sessions WHERE expires<?',(time.time(),))
        con.execute('INSERT INTO sessions VALUES(?,?,?)',(hashlib.sha256(token.encode()).hexdigest(),uid,time.time()+86400*30))
    response=JSONResponse({'email':email,'admin':email in config.ADMINS})
    response.set_cookie('detective_session',token,httponly=True,secure=config.PRODUCTION,samesite='strict',max_age=86400*30)
    return response


@app.post('/api/logout')
def logout(request:Request):
    token=request.cookies.get('detective_session','')
    with db.transaction() as con:con.execute('DELETE FROM sessions WHERE token=?',(hashlib.sha256(token.encode()).hexdigest(),))
    response=JSONResponse({'ok':True});response.delete_cookie('detective_session');return response


@app.get('/api/me')
def me(user=Depends(authenticate)):
    return {'email':user['email'],'admin':user['email'] in config.ADMINS}


def case_summary(c):
    b=json.loads(c['blueprint']) if c['blueprint'] else {}
    attempts=db.all_rows('SELECT id,status,version,created,updated FROM attempts WHERE case_id=? ORDER BY created DESC',(c['id'],))
    assets=db.all_rows("SELECT id,entity_id FROM assets WHERE case_id=? AND kind='location' AND variant='base'",(c['id'],))
    cover=next((a['id'] for a in assets if a['entity_id']==b.get('start_location')),None)
    return {'id':c['id'],'status':c['status'],'settings':json.loads(c['settings']),'title':b.get('title','Новое расследование'),'subtitle':b.get('subtitle',''),
        'cover':cover,'created':c['created'],'attempts':attempts,'spoiled':any(a['status']=='finished' for a in attempts)}


@app.get('/api/cases')
def cases(user=Depends(authenticate)):
    return [case_summary(c) for c in db.all_rows('SELECT * FROM cases WHERE user_id=? ORDER BY updated DESC',(user['id'],))]


@app.post('/api/cases',status_code=202)
def create_case(body:Settings,request:Request,user=Depends(authenticate)):
    k=key(request);payload=body.model_dump();fingerprint=db.digest(payload)
    with db.transaction() as con:
        existing=con.execute('SELECT * FROM cases WHERE user_id=? AND request_key=?',(user['id'],k)).fetchone()
        if existing:
            if existing['request_hash']!=fingerprint:raise HTTPException(409,'Ключ запроса уже использован с другими настройками.')
            return {'id':existing['id'],'status':existing['status']}
        count=con.execute("SELECT count(*) FROM cases WHERE user_id=? AND status IN ('queued','generating')",(user['id'],)).fetchone()[0]
        recent=con.execute('SELECT count(*) FROM cases WHERE user_id=? AND created>?',(user['id'],time.time()-3600)).fetchone()[0]
        if count>=2 or recent>=8:raise HTTPException(429,'Лимит одновременной подготовки дел. Дождитесь готовности или повторите позже.')
        cid=db.uid();now=time.time()
        con.execute('INSERT INTO cases(id,user_id,request_key,request_hash,settings,created,updated) VALUES(?,?,?,?,?,?,?)',(cid,user['id'],k,fingerprint,db.encode(payload),now,now))
        db.enqueue(con,cid,'generate',{},cid+':generate',1)
    return {'id':cid,'status':'queued'}


@app.get('/api/cases/{cid}')
def case_details(cid:str,user=Depends(authenticate)):
    c=owned_case(cid,user);result=case_summary(c)
    job=db.one("SELECT status,error_code,checkpoint FROM jobs WHERE case_id=? AND kind='generate'",(cid,))
    cp=json.loads(job['checkpoint']) if job and job['checkpoint'] else {}
    result['stage']='ready' if c['status']=='ready' else ('review' if 'blueprint' in cp else 'writing')
    result['error']=job['error_code'] if job and job['status']=='failed' else None
    result['jobs']=db.all_rows('SELECT kind,status,count(*) AS count FROM jobs WHERE case_id=? GROUP BY kind,status',(cid,))
    if c['blueprint']:
        b=json.loads(c['blueprint']);result.update({k:b[k] for k in ['introduction','start_time','setting_rules']})
    return result


@app.post('/api/cases/{cid}/retry')
def retry_case(cid:str,user=Depends(authenticate)):
    owned_case(cid,user)
    j=db.one("SELECT id FROM jobs WHERE case_id=? AND kind='generate' AND status='failed'",(cid,))
    if not j:raise HTTPException(409,'Нет неудачной генерации для повтора.')
    return retry_job(j['id'],user,False)


@app.post('/api/cases/{cid}/replay')
def replay(cid:str,request:Request,user=Depends(authenticate)):
    c=owned_case(cid,user)
    if c['status']!='ready':raise HTTPException(409,'Дело ещё не готово.')
    aid=hashlib.sha256((user['id']+cid+key(request)).encode()).hexdigest()[:32]
    with db.transaction() as con:
        if con.execute('SELECT id FROM attempts WHERE id=?',(aid,)).fetchone():return {'id':aid}
        b=json.loads(c['blueprint']);s=world.initial(b);world.observe_people(b,s);now=time.time()
        con.execute('INSERT INTO attempts(id,case_id,user_id,state,initial_state,created,updated) VALUES(?,?,?,?,?,?,?)',(aid,cid,user['id'],db.encode(s),db.encode(s),now,now))
    return {'id':aid}


def permitted_asset(a,b,s):
    if a['kind']=='location':return a['entity_id'] in s['visited']
    if a['kind']=='person':return a['entity_id'] in s['known_people']
    obj=world.index(b,'objects').get(a['entity_id'])
    return obj and (world.visible(obj,s) or any(e['id']==obj['id'] for e in s['evidence']))


@app.get('/api/attempts/{aid}')
def attempt_details(aid:str,user=Depends(authenticate)):
    a=owned_attempt(aid,user);c=owned_case(a['case_id'],user);b=json.loads(c['blueprint']);s=json.loads(a['state'])
    pending=db.one("SELECT id,status FROM commands WHERE attempt_id=? AND status IN ('queued','running')",(aid,))
    assets=[{k:x[k] for k in ['id','kind','entity_id','variant']} for x in db.all_rows('SELECT * FROM assets WHERE case_id=?',(c['id'],)) if permitted_asset(x,b,s)]
    asset_jobs=[]
    for j in db.all_rows("SELECT id,payload,status FROM jobs WHERE case_id=? AND kind='asset'",(c['id'],)):
        p=json.loads(j['payload'])
        if permitted_asset({'kind':p['kind'],'entity_id':p['entity']},b,s):
            asset_jobs.append({'id':j['id'],'kind':p['kind'],'entity_id':p['entity'],'variant':p['variant'],'status':j['status']})
    failed=db.one("SELECT commands.id,commands.result,jobs.id AS job_id FROM commands JOIN jobs ON jobs.command_id=commands.id WHERE attempt_id=? AND commands.status='failed' AND expected_version=? ORDER BY commands.created DESC LIMIT 1",(aid,a['version']))
    return {'asset_jobs':asset_jobs,'failed_command':{'id':failed['id'],'job_id':failed['job_id'],'result':json.loads(failed['result'])} if failed else None,'id':a['id'],'case_id':c['id'],'title':b['title'],'introduction':b['introduction'],'start_time':b['start_time'],'setting_rules':b['setting_rules'],'language':json.loads(c['settings'])['language'],
        'version':a['version'],'status':a['status'],'world':world.public_world(b,s),'assets':assets,'pending':pending,
        'events':[json.loads(e['public'])|{'minute':e['minute'],'event_id':e['id']} for e in db.all_rows('SELECT id,public,minute FROM events WHERE attempt_id=? ORDER BY id',(aid,))],
        'verdict':json.loads(a['verdict']) if a['verdict'] else None,
        'spoiled':bool(db.one("SELECT id FROM attempts WHERE case_id=? AND status='finished' AND id!=?",(c['id'],aid)))}


@app.post('/api/attempts/{aid}/commands',status_code=202)
def command(aid:str,body:CommandInput,request:Request,user=Depends(authenticate)):
    a=owned_attempt(aid,user);payload=body.model_dump();k=key(request);fingerprint=db.digest(payload)
    with db.transaction() as con:
        old=con.execute('SELECT * FROM commands WHERE attempt_id=? AND request_key=?',(aid,k)).fetchone()
        if old:
            if old['request_hash']!=fingerprint:raise HTTPException(409,'Этот ключ относится к другому действию.')
            return {'id':old['id'],'status':old['status']}
        current=con.execute('SELECT * FROM attempts WHERE id=?',(aid,)).fetchone()
        if current['version']!=body.version:raise HTTPException(409,'Состояние изменилось в другой вкладке. Обновите расследование.')
        if current['status']!='active':raise HTTPException(409,'Эта попытка завершена.')
        if con.execute("SELECT id FROM commands WHERE attempt_id=? AND status IN ('queued','running')",(aid,)).fetchone():raise HTTPException(409,'Предыдущее действие ещё выполняется.')
        if body.kind=='finish' and (not body.confirmed or len(body.text.strip())<20):raise HTTPException(422,'Подтвердите раскрытие решения и опишите свою версию.')
        if body.kind=='action' and not body.text.strip():raise HTTPException(422,'Опишите действие или задайте вопрос.')
        try:(world.cited_evidence if body.kind=='finish' else world.accessible_evidence)(json.loads(current['state']),body.evidence)
        except ValueError as e:raise HTTPException(422,str(e))
        cid=db.uid()
        con.execute('INSERT INTO commands(id,attempt_id,user_id,request_key,request_hash,payload,expected_version,created) VALUES(?,?,?,?,?,?,?,?)',(cid,aid,user['id'],k,fingerprint,db.encode(payload),body.version,time.time()))
        db.enqueue(con,a['case_id'],'command',{},cid+':command',0,cid)
    return {'id':cid,'status':'queued'}


@app.get('/api/commands/{cid}')
def command_status(cid:str,user=Depends(authenticate)):
    c=db.one('SELECT * FROM commands WHERE id=? AND user_id=?',(cid,user['id']))
    if not c:raise HTTPException(404)
    j=db.one('SELECT id,status,error_code FROM jobs WHERE command_id=?',(cid,))
    return {'id':c['id'],'status':c['status'],'result':json.loads(c['result']) if c['result'] else None,'job_id':j['id'] if j and j['status']=='failed' else None}


@app.post('/api/attempts/{aid}/notes')
def note(aid:str,body:NoteInput,request:Request,user=Depends(authenticate)):
    owned_attempt(aid,user);nid=key(request)
    with db.transaction() as con:
        a=con.execute('SELECT * FROM attempts WHERE id=?',(aid,)).fetchone();s=json.loads(a['state'])
        old=next((n for n in s['notes'] if n['id']==nid),None)
        if old:
            if old['text']!=body.text or old['kind']!=body.kind or old['links']!=body.links:raise HTTPException(409,'Ключ заметки уже использован.')
            return old
        if a['version']!=body.version:raise HTTPException(409,'Состояние изменилось. Обновите страницу.')
        if con.execute("SELECT id FROM commands WHERE attempt_id=? AND status IN ('queued','running')",(aid,)).fetchone():raise HTTPException(409,'Дождитесь завершения действия.')
        ids={e['id'] for e in s['evidence']}|set(s['known_people'])|{n['id'] for n in s['notes']}
        if set(body.links)-ids:raise HTTPException(422,'Неизвестный источник заметки.')
        n={'id':nid,'text':body.text,'kind':body.kind,'links':body.links,'minute':s['minute']}
        s['notes'].append(n)
        con.execute('UPDATE attempts SET state=?,version=version+1,updated=? WHERE id=?',(db.encode(s),time.time(),aid))
        return n


@app.get('/api/assets/{asset_id}')
def asset(asset_id:str,user=Depends(authenticate)):
    a=db.one('SELECT assets.* FROM assets JOIN cases ON cases.id=assets.case_id WHERE assets.id=? AND cases.user_id=?',(asset_id,user['id']))
    if not a:raise HTTPException(404)
    c=owned_case(a['case_id'],user);b=json.loads(c['blueprint'])
    attempts=db.all_rows('SELECT state FROM attempts WHERE case_id=? AND user_id=?',(a['case_id'],user['id']))
    # A player who previously observed an asset can access it; never raw storage URLs.
    allowed=any(permitted_asset(a,b,json.loads(x['state'])) for x in attempts)
    if not allowed:raise HTTPException(404)
    return FileResponse(config.DATA/a['path'],media_type='image/png',headers={'Cache-Control':'private, max-age=3600'})


def retry_job(jid,user,is_admin):
    with db.transaction() as con:
        j=con.execute('SELECT jobs.*,cases.user_id FROM jobs JOIN cases ON cases.id=jobs.case_id WHERE jobs.id=?',(jid,)).fetchone()
        if not j or (not is_admin and j['user_id']!=user['id']):raise HTTPException(404)
        if j['status']!='failed':raise HTTPException(409,'Операция не находится в состоянии ошибки.')
        retries=con.execute("SELECT count(*) FROM audit WHERE target=? AND action='retry'",(jid,)).fetchone()[0]
        if retries>=3 and not is_admin:raise HTTPException(429,'Повторы исчерпаны. Обратитесь к администратору.')
        if j['command_id']:
            c=con.execute('SELECT * FROM commands WHERE id=?',(j['command_id'],)).fetchone()
            a=con.execute('SELECT * FROM attempts WHERE id=?',(c['attempt_id'],)).fetchone()
            if a['version']!=c['expected_version'] or a['status']!='active':raise HTTPException(409,'Мир уже изменился. Сформулируйте новое действие.')
            if con.execute("SELECT id FROM commands WHERE attempt_id=? AND status IN ('queued','running')",(a['id'],)).fetchone():raise HTTPException(409,'Уже выполняется другая команда.')
            con.execute("UPDATE commands SET status='queued',result=NULL WHERE id=?",(j['command_id'],))
        con.execute("UPDATE jobs SET status='queued',attempts=0,repair_count=0,available=?,error_code=NULL,updated=? WHERE id=?",(time.time(),time.time(),jid))
        if j['kind']=='generate':con.execute("UPDATE cases SET status='queued' WHERE id=?",(j['case_id'],))
        con.execute('INSERT INTO audit(user_id,action,target,created) VALUES(?,?,?,?)',(user['id'],'retry',jid,time.time()))
    return {'ok':True}


@app.post('/api/jobs/{jid}/retry')
def user_retry_job(jid:str,user=Depends(authenticate)):
    return retry_job(jid,user,False)


@app.get('/api/admin/jobs')
def admin_jobs(user=Depends(admin)):
    return {'jobs':db.all_rows("SELECT id,case_id,kind,status,attempts,repair_count,error_code,diagnostic,created,updated FROM jobs WHERE status!='done' ORDER BY created DESC LIMIT 100"),
        'usage':db.all_rows('SELECT category,model,status,count(*) AS calls,sum(input_tokens) AS input_tokens,sum(output_tokens) AS output_tokens,avg(elapsed) AS avg_seconds FROM operations GROUP BY category,model,status'),
        'audit':db.all_rows('SELECT * FROM audit ORDER BY id DESC LIMIT 50')}


@app.post('/api/admin/jobs/{jid}/retry')
def admin_retry(jid:str,user=Depends(admin)):
    return retry_job(jid,user,True)


app.mount('/static',StaticFiles(directory=config.ROOT/'static'),name='static')


@app.get('/{path:path}')
def frontend(path:str):
    return FileResponse(config.ROOT/'static'/'index.html')
