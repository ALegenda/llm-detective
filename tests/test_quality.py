import time
import json
import pytest
from app import db, worker
from app.ai import AI, InvalidContent, ProviderFailure
from test_reliability import queue_job


def report(credits):
    return {'claims':[{'quote':'Моя версия.','status':'accurate','feedback':'Верно'}],
            'criteria':[{'criterion_index':i,'satisfied':c==2,'credit':c,
                         'quote':'Моя версия.' if c else '',
                         'evidence_ids':['f1'] if c==2 else [],'feedback':'Разбор'} for i,c in enumerate(credits)],
            'evidence_assessment':[]}


@pytest.mark.parametrize('credits,score,proved', [([0,0,0,0],0,False),([2,1,0,1],5,False),([2,2,2,1],8.8,False),([2,2,2,2],10,True)])
def test_report_partial_credit_is_explained_and_deterministic(credits,score,proved):
    result=worker.grounded_evaluation(report(credits),'Моя версия.',['f1'],[{'description':f'Критерий {i}'} for i in range(4)])
    assert result['score']==score and result['proved']==proved
    assert [c['credit'] for c in result['criteria']]==credits
    assert result['criteria'][0]['description']=='Критерий 0'


def test_wrong_claim_deducts_without_erasing_correct_work():
    raw=report([2,2]);raw['claims'][0]['status']='mistaken'
    result=worker.grounded_evaluation(raw,'Моя версия.',['f1'],[{'description':'Кто'},{'description':'Как'}])
    assert result['score']==9.5 and result['mistake_deduction']==0.5 and not result['proved']


def test_cannot_award_partial_credit_without_player_statement():
    raw=report([1]);raw['criteria'][0]['quote']=''
    with pytest.raises(InvalidContent,match='credit'):worker.grounded_evaluation(raw,'Моя версия.',['f1'],[{'description':'Кто'}])


def test_new_cases_do_not_grade_identity_and_motive_twice():
    criteria=[{'aspect':aspect,'description':aspect,'evidence_ids':['f1']} for aspect in ['identity','method','motive']]
    rubric=worker.evaluation_rubric({'criteria':criteria})
    assert rubric==criteria
    # Establishing motive earns its share even when identity/method are missing;
    # an extra combined method-and-motive criterion must not dilute it again.
    result=worker.grounded_evaluation(report([0,0,2]),'Моя версия.',['f1'],rubric)
    assert result['score']==3.3 and not result['proved']


def feedback():
    return dict(fairness=2,discoveries=1,agency=2,characters=1,pacing=2,highlight='Сопоставление',frustration='Повторы')


def test_feedback_requires_finish_and_is_owned_idempotent_and_separate(client):
    assert client.post('/api/attempts/a1/feedback',json=feedback()).status_code==409
    with db.transaction() as con:con.execute("UPDATE attempts SET status='finished' WHERE id='a1'")
    before=db.one("SELECT state,verdict,version FROM attempts WHERE id='a1'")
    assert client.get('/api/attempts/a1').json()['story_feedback'] is None
    assert client.post('/api/attempts/a1/feedback',json=feedback()).json()['score']==8
    updated=feedback()|{'discoveries':2}
    assert client.post('/api/attempts/a1/feedback',json=updated).json()['score']==9
    assert len(db.all_rows('SELECT * FROM story_feedback'))==1
    assert client.get('/api/attempts/a1').json()['story_feedback']['score']==9
    assert db.one("SELECT state,verdict,version FROM attempts WHERE id='a1'")==before
    assert client.post('/api/attempts/a1/feedback',json=updated|{'fairness':10}).status_code==422
    assert client.post('/api/attempts/a1/feedback',json=updated|{'fairness':True}).status_code==422
    from app.main import app, authenticate
    app.dependency_overrides[authenticate]=lambda: {'id':'another-user','email':'another@test.invalid'}
    assert client.post('/api/attempts/a1/feedback',json=updated).status_code==404


def test_base_portrait_does_not_eagerly_enqueue_six_emotions(game):
    job=queue_job('asset',{'kind':'person','entity':'n_ira','variant':'base'})
    class ControlledAI:
        case=db.one("SELECT * FROM cases WHERE id='c1'")
        def image(self,*args,**kwargs):return b'controlled image bytes'
        def structured(self,*args,**kwargs):return {'accepted':True,'reason':'Valid portrait'}
    worker.asset_job(job,ControlledAI())
    assert len(db.all_rows('SELECT * FROM assets'))==1
    assert len(db.all_rows("SELECT * FROM jobs WHERE kind='asset'"))==1


def test_visual_review_retry_reuses_rendered_image(game):
    job=queue_job('asset',{'kind':'object','entity':'o_key','variant':'base'})
    class ControlledAI:
        case=db.one("SELECT * FROM cases WHERE id='c1'")
        calls=0
        review_calls=0
        def image(self,*args,**kwargs):
            self.calls+=1
            return b'controlled image bytes'
        def structured(self,*args,**kwargs):
            self.review_calls+=1
            if self.review_calls==1:raise ProviderFailure('provider_429',True)
            return {'accepted':True,'reason':'Valid object'}
    ai=ControlledAI()
    with pytest.raises(ProviderFailure):worker.asset_job(job,ai)
    resumed=db.one('SELECT * FROM jobs WHERE id=?',(job['id'],))
    worker.asset_job(resumed,ai)
    assert ai.calls==1 and len(db.all_rows('SELECT * FROM assets'))==1


def test_rejected_document_redraw_drops_conflicting_content_reference(game):
    import json
    job=queue_job('asset',{'kind':'object','entity':'o_letter','variant':'base'})
    class ControlledAI:
        case=db.one("SELECT * FROM cases WHERE id='c1'")
        prompts=[]
        def image(self,prompt,*args,**kwargs):
            self.prompts.append(prompt)
            return b'controlled image'
        def structured(self,*args,**kwargs):
            return {'accepted':len(self.prompts)>1,'reason':'Invented handwritten dates'}
    ai=ControlledAI();blueprint=json.loads(ai.case['blueprint'])
    next(o for o in blueprint['objects'] if o['id']=='o_letter')['image_prompt']='A filled table, signature and readable 18:00'
    ai.case['blueprint']=db.encode(blueprint)
    with pytest.raises(InvalidContent):worker.asset_job(job,ai)
    worker.asset_job(db.one('SELECT * FROM jobs WHERE id=?',(job['id'],)),ai)
    assert len(ai.prompts)==2 and 'readable 18:00' in ai.prompts[0] and 'readable 18:00' not in ai.prompts[1]
    assert 'BACK side' in ai.prompts[1]


def test_rejected_location_redraw_drops_repeated_focal_prop(game):
    job=queue_job('asset',{'kind':'location','entity':'l_hall','variant':'base'})
    class ControlledAI:
        case=db.one("SELECT * FROM cases WHERE id='c1'")
        prompts=[]
        def image(self,prompt,*args,**kwargs):
            self.prompts.append(prompt)
            return b'controlled image'
        def structured(self,*args,**kwargs):
            return {'accepted':len(self.prompts)>1,'reason':'Dominant optical instrument'}
    ai=ControlledAI();blueprint=json.loads(ai.case['blueprint'])
    blueprint['locations'][0]['image_prompt']='A dominant optical instrument on the workbench'
    ai.case['blueprint']=db.encode(blueprint)
    with pytest.raises(InvalidContent):worker.asset_job(job,ai)
    worker.asset_job(db.one('SELECT * FROM jobs WHERE id=?',(job['id'],)),ai)
    assert 'optical instrument' in ai.prompts[0]
    assert 'optical instrument' not in ai.prompts[1]
    assert 'Keep work surfaces clear' in ai.prompts[1]
    assert blueprint['locations'][0]['description'] in ai.prompts[1]
    assert db.one('SELECT status FROM jobs WHERE id=?',(job['id'],))['status']=='done'


def test_rate_limit_waits_for_provider_window_instead_of_exhausting_in_seconds(game):
    job=queue_job('asset');job['attempts']=3
    now=time.time();worker.fail_job(job,ProviderFailure('provider_429',True,270))
    saved=db.one('SELECT * FROM jobs WHERE id=?',(job['id'],))
    assert saved['status']=='retry' and saved['available']>=now+270
    job=queue_job('asset');job['attempts']=6
    worker.fail_job(job,ProviderFailure('provider_429',True))
    assert db.one('SELECT status FROM jobs WHERE id=?',(job['id'],))['status']=='failed'


def test_zero_budgets_allow_authorized_test_window(game,monkeypatch):
    for key in ['CASE_CALL_LIMIT','ATTEMPT_CALL_LIMIT','DAILY_CALL_LIMIT']:monkeypatch.setenv(key,'0')
    monkeypatch.setenv('OPENAI_API_KEY','test-no-provider-call')
    job=queue_job('asset');ai=AI(job)
    assert ai.start('image','controlled-model')
    monkeypatch.setenv('DAILY_CALL_LIMIT','1')
    with pytest.raises(ProviderFailure,match='budget_limit'):ai.start('image','controlled-model')


def test_long_generation_cannot_occupy_the_interactive_worker(game):
    with db.transaction() as con:
        db.enqueue(con,'c1','generate',{},'story-first',1)
        db.enqueue(con,'c1','asset',{},'art-second',2)
        db.enqueue(con,'c1','command',{},'player-third',10)
    # Even with a higher-priority story waiting, input owns its own lane.
    assert db.claim('interactive')['kind']=='command'
    assert db.claim('interactive') is None
    assert db.claim('generation')['kind']=='generate'
    assert db.claim('assets')['kind']=='asset'
