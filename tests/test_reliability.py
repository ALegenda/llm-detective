import concurrent.futures
import copy
import json
import time
import pytest
from app import db, world, worker
from app.ai import ProviderFailure, InvalidContent
from conftest import step


def queue_job(kind='command',payload=None,command_id=None):
    with db.transaction() as con:db.enqueue(con,'c1',kind,payload or {},db.uid(),command_id=command_id)
    return db.claim()


def test_lease_claim_is_exclusive_and_stale_fenced(game):
    with db.transaction() as con:db.enqueue(con,'c1','asset',{},'dedupe')
    with concurrent.futures.ThreadPoolExecutor(2) as pool:
        claimed=list(pool.map(lambda _:db.claim(),range(2)))
    first=next(j for j in claimed if j)
    assert sum(j is not None for j in claimed)==1
    with db.transaction() as con:con.execute('UPDATE jobs SET lease_until=? WHERE id=?',(time.time()-1,first['id']))
    second=db.claim();assert second['lease_token']!=first['lease_token']
    with db.transaction() as con:
        assert not db.fenced(con,first) and db.fenced(con,second)


def test_checkpoint_survives_worker_restart(game):
    first=queue_job('asset')
    db.save_checkpoint(first,{'file':'asset-reference'})
    with db.transaction() as con:con.execute('UPDATE jobs SET lease_until=0 WHERE id=?',(first['id'],))
    second=db.claim()
    assert json.loads(second['checkpoint'])=={'file':'asset-reference'}


@pytest.mark.parametrize('error,status',[(ProviderFailure('provider_429',True),'retry'),(ProviderFailure('provider_401'),'failed'),(InvalidContent('bad refs'),'retry')])
def test_error_categories(game,error,status):
    job=queue_job('asset');worker.fail_job(job,error)
    row=db.one('SELECT * FROM jobs WHERE id=?',(job['id'],))
    assert row['status']==status
    if isinstance(error,InvalidContent):assert row['repair_count']==1


def test_invalid_content_repair_is_bounded(game):
    job=queue_job('asset');job['repair_count']=2
    worker.fail_job(job,InvalidContent('bad refs'))
    assert db.one('SELECT status FROM jobs WHERE id=?',(job['id'],))['status']=='failed'


def test_public_creation_is_idempotent_and_transactional(client):
    settings={'theme':'Тайна на станции','difficulty':'hard','duration':'short','language':'ru'}
    r=client.post('/api/cases',json=settings,headers={'Idempotency-Key':'create-test-001'})
    assert r.status_code==202
    cid=r.json()['id']
    assert client.post('/api/cases',json=settings,headers={'Idempotency-Key':'create-test-001'}).json()['id']==cid
    assert len(db.all_rows('SELECT * FROM jobs WHERE case_id=?',(cid,)))==1
    assert client.post('/api/cases',json=settings|{'theme':'Other'},headers={'Idempotency-Key':'create-test-001'}).status_code==409


def test_parallel_tabs_one_command_and_same_key_replay(client):
    body={'text':'Осматриваю окно','version':0}
    r=client.post('/api/attempts/a1/commands',json=body,headers={'Idempotency-Key':'action-one'})
    assert r.status_code==202
    assert client.post('/api/attempts/a1/commands',json=body,headers={'Idempotency-Key':'action-one'}).json()==r.json()
    assert client.post('/api/attempts/a1/commands',json=body,headers={'Idempotency-Key':'action-two'}).status_code==409
    assert client.post('/api/attempts/a1/commands',json=body|{'text':'Другое'},headers={'Idempotency-Key':'action-one'}).status_code==409


def test_lost_response_replays_without_new_turn(client,game,monkeypatch):
    b,s=game;body={'text':'Беру ключ','version':0}
    r=client.post('/api/attempts/a1/commands',json=body,headers={'Idempotency-Key':'lost-response'})
    cid=r.json()['id'];j=db.claim()
    new,result,mut=world.reduce(b,s,[step('take','o_key')],{'evidence':[],'text':'Беру ключ'})
    monkeypatch.setattr(worker,'prepare_command',lambda job,ai:{'state':new,'result':result,'mutations':mut})
    class FakeAI:case=db.one('SELECT * FROM cases WHERE id=?',('c1',))
    worker.command_job(j,FakeAI())
    repeat=client.post('/api/attempts/a1/commands',json=body,headers={'Idempotency-Key':'lost-response'})
    assert repeat.json()['id']==cid
    worker.command_job(j,FakeAI())
    a=client.get('/api/attempts/a1').json()
    assert a['version']==1 and a['world']['minute']==1 and a['world']['inventory']==['o_key']
    assert len(db.all_rows('SELECT * FROM events WHERE attempt_id=?',('a1',)))==1


def test_stale_prepared_result_cannot_overwrite(client,game,monkeypatch):
    b,s=game;r=client.post('/api/attempts/a1/commands',json={'text':'Беру ключ','version':0},headers={'Idempotency-Key':'stale-result'})
    j=db.claim()
    monkeypatch.setattr(worker,'prepare_command',lambda job,ai:{'state':s,'result':{'messages':[],'minutes':0},'mutations':[]})
    with db.transaction() as con:con.execute('UPDATE attempts SET version=1 WHERE id=?',('a1',))
    class FakeAI:case=db.one('SELECT * FROM cases WHERE id=?',('c1',))
    with pytest.raises(ProviderFailure):worker.command_job(j,FakeAI())
    assert db.one('SELECT version FROM attempts WHERE id=?',('a1',))['version']==1


def test_asset_failure_does_not_cancel_playable_case(game):
    j=queue_job('asset');worker.fail_job(j,ProviderFailure('provider_403'))
    assert db.one('SELECT status FROM cases WHERE id=?',('c1',))['status']=='ready'


def test_note_idempotency_and_no_time(client):
    payload={'text':'Моя гипотеза','kind':'hypothesis','links':[],'version':0}
    r=client.post('/api/attempts/a1/notes',json=payload,headers={'Idempotency-Key':'note-one'})
    assert r.status_code==200
    assert client.post('/api/attempts/a1/notes',json=payload,headers={'Idempotency-Key':'note-one'}).status_code==200
    a=client.get('/api/attempts/a1').json()
    assert len(a['world']['notes'])==1 and a['world']['minute']==0


def test_restart_same_truth_old_attempt_preserved(client):
    a=client.post('/api/cases/c1/replay',headers={'Idempotency-Key':'replay-one'}).json()['id']
    b=client.post('/api/cases/c1/replay',headers={'Idempotency-Key':'replay-one'}).json()['id']
    assert a==b and a!='a1'
    assert client.get('/api/attempts/a1').status_code==200
    fresh=client.get('/api/attempts/'+a).json()
    assert fresh['world']['minute']==0 and fresh['world']['inventory']==[]


def test_ownership_csrf_and_private_assets(client,game):
    from app.main import app,authenticate
    assert client.post('/api/cases',json={'theme':'test'},headers={'X-Requested-With':'bad'}).status_code==403
    assert client.post('/api/cases',json={'theme':'test'},headers={'Origin':'https://attacker.invalid'}).status_code==403
    app.dependency_overrides[authenticate]=lambda:{'id':'other','email':'other@example.test'}
    assert client.get('/api/cases/c1').status_code==404
    assert client.get('/api/attempts/a1').status_code==404
    assert client.get('/api/assets/private-image').status_code==404
    assert client.get('/api/admin/jobs').status_code==403


def test_no_truth_in_progress_events_or_attempt(client):
    for url in ['/api/cases','/api/cases/c1','/api/attempts/a1']:
        text=client.get(url).text
        assert 'culprits' not in text and 'private_context' not in text and 'редкий цветок' not in text


def test_finish_requires_explicit_confirmation(client):
    body={'kind':'finish','text':'Моя версия достаточно длинная для проверки.','version':0}
    assert client.post('/api/attempts/a1/commands',json=body,headers={'Idempotency-Key':'finish-one'}).status_code==422


def test_backup_restore_preserves_save_and_schema(game,isolated,tmp_path,monkeypatch):
    from scripts.backup import backup,restore
    from app import config
    target=tmp_path/'backup';restored=tmp_path/'restored'
    before=db.one('SELECT state FROM attempts WHERE id=?',('a1',))['state']
    backup(isolated,target);restore(target,restored)
    monkeypatch.setattr(config,'DB_PATH',restored/'detective.sqlite3')
    db.init()  # Opening after schema initialization is non-destructive.
    assert db.one('SELECT state FROM attempts WHERE id=?',('a1',))['state']==before


def test_final_explanation_can_cite_a_dropped_item(game):
    b,s=game
    s,_,_=world.reduce(b,s,[step('take','o_key'),step('put','o_key')],{'text':'Ключ','evidence':[]})
    assert world.cited_evidence(s,['o_key'])==['o_key']
    with pytest.raises(ValueError):world.accessible_evidence(s,['o_key'])


def test_failed_command_remains_recoverable_after_reload(client):
    response=client.post('/api/attempts/a1/commands',json={'text':'Осматриваюсь','version':0},headers={'Idempotency-Key':'failure-reload'})
    j=db.claim();worker.fail_job(j,ProviderFailure('provider_401'))
    a=client.get('/api/attempts/a1').json()
    assert a['failed_command']['id']==response.json()['id']
    assert a['failed_command']['job_id']==j['id']
    assert a['world']['minute']==0 and a['pending'] is None
    assert client.post('/api/jobs/'+j['id']+'/retry').status_code==200
    assert client.get('/api/attempts/a1').json()['failed_command'] is None


def test_schema_one_migration_keeps_existing_save(game):
    saved=db.one('SELECT state FROM attempts WHERE id=?',('a1',))['state']
    with db.transaction() as con:
        con.execute('ALTER TABLE operations DROP COLUMN cache_key')
        con.execute('ALTER TABLE operations DROP COLUMN response')
        con.execute('PRAGMA user_version=1')
    db.init()
    assert db.one('SELECT state FROM attempts WHERE id=?',('a1',))['state']==saved
    with db.transaction() as con:
        assert con.execute('PRAGMA user_version').fetchone()[0]==2
        assert {'cache_key','response'} <= {r[1] for r in con.execute('PRAGMA table_info(operations)')}


def test_generation_repairs_saved_draft_with_precise_feedback(game,blueprint):
    from app.generation import validate_blueprint
    bad=copy.deepcopy(blueprint);bad['checks'][0]['object_id']='l_hall'
    with pytest.raises(ValueError) as error:validate_blueprint(bad)
    j=queue_job('generate')
    db.save_checkpoint(j,{'draft':bad,'feedback':[str(error.value)]})
    j=db.one('SELECT * FROM jobs WHERE id=?',(j['id'],))
    class ControlledAI:
        case=db.one('SELECT * FROM cases WHERE id=?',('c1',))
        calls=[]
        def structured(self,category,prompt,context,schema):
            self.calls.append(category)
            if category=='blueprint':
                assert context['previous_draft']==bad
                assert 'f_lock.object_id' in context['repair_feedback'][0]
                return blueprint
            return {'accepted':True,'issues':[],'routes':['letter then view','view then letter']}
    ai=ControlledAI();worker.generate(j,ai)
    assert ai.calls==['blueprint','case_review']
    assert db.one('SELECT status FROM jobs WHERE id=?',(j['id'],))['status']=='done'


def test_generation_normalizes_missing_reverse_exit_without_retry(game,blueprint):
    one_sided=copy.deepcopy(blueprint)
    one_sided['locations'][1]['exits']=[]
    j=queue_job('generate')
    class ControlledAI:
        case=db.one('SELECT * FROM cases WHERE id=?',('c1',))
        calls=[]
        def structured(self,category,prompt,context,schema):
            self.calls.append(category)
            if category=='blueprint':
                return one_sided
            garden=next(x for x in context['blueprint']['locations'] if x['id']=='l_garden')
            assert garden['exits']==['l_hall']
            return {'accepted':True,'issues':[],'alternative_routes':['letter then view','view then letter'],'reasoning_quality':'fair'}
    ai=ControlledAI();worker.generate(j,ai)
    saved=db.one('SELECT * FROM jobs WHERE id=?',(j['id'],))
    assert ai.calls==['blueprint','case_review']
    assert saved['status']=='done' and saved['repair_count']==0


@pytest.mark.parametrize('invalid_room', [False, True])
def test_generation_reuses_saved_draft_when_new_validator_can_normalize_it(game,blueprint,invalid_room):
    one_sided=copy.deepcopy(blueprint)
    one_sided['locations'][1]['exits']=[]
    if invalid_room:one_sided['objects'][2]['location']='l_safe'
    j=queue_job('generate')
    db.save_checkpoint(j,{'draft':one_sided,'feedback':['Exits must exist and be reciprocal']})
    j=db.one('SELECT * FROM jobs WHERE id=?',(j['id'],))
    class ControlledAI:
        case=db.one('SELECT * FROM cases WHERE id=?',('c1',))
        calls=[]
        def structured(self,category,prompt,context,schema):
            self.calls.append(category)
            assert category=='case_review'
            garden=next(x for x in context['blueprint']['locations'] if x['id']=='l_garden')
            assert garden['exits']==['l_hall']
            assert context['blueprint']['objects'][2]['location']=='l_hall'
            return {'accepted':True,'issues':[],'alternative_routes':['letter then view','view then letter'],'reasoning_quality':'fair'}
    ai=ControlledAI();worker.generate(j,ai)
    assert ai.calls==['case_review']
    assert db.one('SELECT status FROM jobs WHERE id=?',(j['id'],))['status']=='done'


def test_structurally_valid_draft_still_rewrites_rejected_plot(game,blueprint):
    j=queue_job('generate')
    db.save_checkpoint(j,{'draft':blueprint,'feedback':['Essential testimony contradicts the timeline'],'needs_rewrite':True})
    j=db.one('SELECT * FROM jobs WHERE id=?',(j['id'],))
    class ControlledAI:
        case=db.one('SELECT * FROM cases WHERE id=?',('c1',))
        calls=[]
        def structured(self,category,prompt,context,schema):
            self.calls.append(category)
            if category=='blueprint':
                assert context['repair_feedback']==['Essential testimony contradicts the timeline']
                assert context['previous_draft']==blueprint
                return blueprint
            return {'accepted':True,'issues':[],'alternative_routes':[],'reasoning_quality':'fair'}
    ai=ControlledAI();worker.generate(j,ai)
    assert ai.calls==['blueprint','case_review']


def test_art_budget_cannot_consume_action_budget(client,monkeypatch):
    from app.ai import AI
    from app import config
    monkeypatch.setenv('OPENAI_API_KEY','test-only-no-request')
    monkeypatch.setenv('CASE_CALL_LIMIT','1')
    monkeypatch.setenv('ATTEMPT_CALL_LIMIT','1')
    asset=queue_job('asset'); ai=AI(asset)
    ai.start('image',config.IMAGE_MODEL)
    with pytest.raises(ProviderFailure,match='budget_limit'):ai.start('image',config.IMAGE_MODEL)
    client.post('/api/attempts/a1/commands',json={'text':'Осматриваюсь','version':0},headers={'Idempotency-Key':'separate-budget'})
    job=db.claim();ai=AI(job)
    assert ai.start('interpret',config.TEXT_MODEL)
    with pytest.raises(ProviderFailure,match='budget_limit'):ai.start('interpret',config.TEXT_MODEL)


def test_evaluation_cannot_attribute_hidden_truth_to_player():
    raw={'claims':[{'quote':'Саша украла рукопись','status':'mistaken','feedback':'Не доказано'}],'criteria':[{'criterion_index':0,'satisfied':False,'quote':'','evidence_ids':[],'feedback':'Не установлена личность'}],'evidence_assessment':[]}
    rubric=[{'description':'Личность'}]
    with pytest.raises(InvalidContent,match='actually written'):
        worker.grounded_evaluation(raw,'Рукопись перенесли. Кто это сделал, я не установил.',['f1'],rubric)
    raw['claims']=[{'quote':'Рукопись перенесли','status':'accurate','feedback':'Следы подтверждают перенос'}]
    result=worker.grounded_evaluation(raw,'Рукопись перенесли. Кто это сделал, я не установил.',['f1'],rubric)
    assert not result['proved'] and not result['mistaken'] and result['missing']


def test_evaluation_cannot_move_the_rubric_or_cite_unprovided_evidence():
    text='Ирина перенесла письмо.'
    rubric=[{'description':'Кто перенёс письмо'}]
    raw={'claims':[{'quote':text,'status':'accurate','feedback':'Подтверждено'}],
         'criteria':[{'criterion_index':0,'satisfied':True,'quote':text,'evidence_ids':['f1'],'feedback':'Достаточно'}],'evidence_assessment':[]}
    assert worker.grounded_evaluation(raw,text,['f1'],rubric)['proved']
    with pytest.raises(InvalidContent,match='did not provide'):worker.grounded_evaluation(raw,text,[],rubric)
    raw['criteria'].append(dict(raw['criteria'][0],criterion_index=1))
    with pytest.raises(InvalidContent,match='fixed rubric'):worker.grounded_evaluation(raw,text,['f1'],rubric)


def test_finish_pipeline_persists_rubric_grounded_verdict(client,game):
    b,s=game
    explanation='Ирина перенесла письмо без взлома, чтобы скрыть перенос встречи.'
    for check in b['checks']:
        world.add_evidence(s,check['id'],check['intent'],check['result'],'observation','Контрольный источник')
    with db.transaction() as con:con.execute('UPDATE attempts SET state=? WHERE id=?',(db.encode(s),'a1'))
    evidence=[c['id'] for c in b['checks']]
    response=client.post('/api/attempts/a1/commands',json={'kind':'finish','text':explanation,'evidence':evidence,'confirmed':True,'version':0},headers={'Idempotency-Key':'finish-rubric'})
    assert response.status_code==202
    job=db.claim()
    class ControlledAI:
        case=db.one('SELECT * FROM cases WHERE id=?',('c1',))
        def structured(self,category,prompt,context,schema):
            assert category=='evaluation'
            raw={'claims':[{'quote':explanation,'status':'accurate','feedback':'Верно'}],
                 'criteria':[{'criterion_index':i,'satisfied':True,'quote':explanation,'evidence_ids':evidence,'feedback':'Подтверждено'} for i in range(len(context['rubric']))],
                 'evidence_assessment':['Сопоставлены независимые источники.']}
            # Exercise the actual dynamic response schema before the reducer.
            return schema.model_validate(raw).model_dump()
    worker.command_job(job,ControlledAI())
    finished=client.get('/api/attempts/a1').json()
    assert finished['status']=='finished' and finished['verdict']['evaluation']['proved']
    assert finished['world']['minute']==0 and finished['version']==1
    assert len(finished['verdict']['evaluation']['criteria'])==4
    assert finished['verdict']['truth']==b['truth']


def test_chat_message_is_speech_not_a_physical_action(client,game):
    text='Я беру ключ и ухожу в сад. Что вы знаете о письме?'
    body={'kind':'talk','target':'n_ira','text':text,'version':0}
    response=client.post('/api/attempts/a1/commands',json=body,headers={'Idempotency-Key':'chat-explicit-1'})
    assert response.status_code==202
    job=db.claim()
    class ControlledAI:
        case=db.one('SELECT * FROM cases WHERE id=?',('c1',))
        calls=[]
        def structured(self,category,prompt,context,schema):
            self.calls.append(category)
            assert category!='interpret'
            if category=='dialogue':
                assert context['request']==text
                assert context['person']['id']=='n_ira'
                return {'reply':'Я была в саду в шесть.','account_ids':['s_time'],'emotion':'calm','attitude':'neutral'}
            return {'grounded':True,'reason':'Matches authored account'}
    ai=ControlledAI();worker.command_job(job,ai)
    saved=client.get('/api/attempts/a1').json()
    assert ai.calls==['dialogue','dialogue_audit']
    assert saved['world']['inventory']==[]
    assert saved['world']['location']=='l_hall'
    assert saved['world']['minute']==2
    assert saved['world']['dialogue'][0]['player']==text
    assert saved['world']['dialogue'][0]['person']=='n_ira'
    assert client.post('/api/attempts/a1/commands',json=body,headers={'Idempotency-Key':'chat-explicit-1'}).json()['id']==response.json()['id']
    assert len(client.get('/api/attempts/a1').json()['world']['dialogue'])==1


@pytest.mark.parametrize('target', ['n_lev','n_missing',''])
def test_chat_requires_present_person(client,target):
    response=client.post('/api/attempts/a1/commands',json={'kind':'talk','target':target,'text':'Добрый вечер','version':0},headers={'Idempotency-Key':'absent-chat'})
    assert response.status_code==422
    assert not db.all_rows('SELECT * FROM commands')


def test_old_case_has_public_briefing_without_hidden_solution(client):
    briefing=client.get('/api/attempts/a1').json()['briefing']
    assert briefing['introduction']=='Письмо пропало. Исследуйте кабинет.'
    assert briefing['objective']
    assert [n['id'] for n in briefing['participants']]==['n_ira']
    public=json.dumps(briefing,ensure_ascii=False)
    assert 'Она перенесла' not in public and 'Лжёт' not in public and 'редкий цветок' not in public
