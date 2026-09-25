import copy
import json
import pytest
from app import db, world, worker
from app.ai import InvalidContent
from conftest import step

P={'text':'Проверяю','evidence':[]}


def test_object_button_bypasses_interpreter_and_preserves_idempotency(client,game):
    body={'kind':'object','object_action':'take','target':'o_key','text':'Беру ключ','version':0}
    r=client.post('/api/attempts/a1/commands',json=body,headers={'Idempotency-Key':'take-object-1'})
    assert r.status_code==202
    class NoAI:
        case=db.one('SELECT * FROM cases WHERE id=?',('c1',))
        def structured(self,*args):raise AssertionError('Explicit actions need no language interpretation')
    worker.command_job(db.claim(),NoAI())
    saved=client.get('/api/attempts/a1').json()
    assert saved['world']['inventory']==['o_key']
    assert saved['world']['minute']==1
    assert saved['events'][-1]['evidence_ids']==['o_key']
    assert client.post('/api/attempts/a1/commands',json=body,headers={'Idempotency-Key':'take-object-1'}).json()['id']==r.json()['id']
    assert client.get('/api/attempts/a1').json()['world']['minute']==1


@pytest.mark.parametrize('target,check', [('o_letter','f_letter'),('o_desk','f_letter'),('o_desk','missing')])
def test_explicit_checks_cannot_access_hidden_or_unrelated_evidence(client,target,check):
    r=client.post('/api/attempts/a1/commands',json={'kind':'object','object_action':'check','target':target,'check_id':check,'version':0},headers={'Idempotency-Key':'invalid-check-1'})
    assert r.status_code==422
    assert not db.all_rows('SELECT * FROM commands')


def test_check_actions_only_reveal_ready_intents_and_not_results(game):
    b,s=game
    b['checks'][0]['requires_tools']=['o_key']
    assert not world.available_checks(b,s,'o_desk')
    s,_,_=world.reduce(b,s,[step('take','o_key')],P)
    checks=world.available_checks(b,s,'o_desk')
    assert checks==[{'id':'f_lock','label':'Осмотреть замок на следы','minutes':2,'done':False}]
    assert 'Следов взлома нет' not in json.dumps(world.public_world(b,s),ensure_ascii=False)


def test_repeated_checks_and_open_close_do_not_waste_time(game):
    b,s=game
    s,_,_=world.reduce(b,s,[step('check','o_desk','f_lock')],P)
    minute=s['minute']
    again,result,_=world.reduce(b,s,[step('check','o_desk','f_lock')],P)
    assert again['minute']==minute and len(again['evidence'])==1 and result['evidence_ids']==[]
    s,_,_=world.reduce(b,s,[step('take','o_key'),step('open','o_desk')],P)
    again,result,_=world.reduce(b,s,[step('open','o_desk')],P)
    assert result['minutes']==0
    s,_,_=world.reduce(b,s,[step('close','o_desk')],P)
    _,result,_=world.reduce(b,s,[step('close','o_desk')],P)
    assert result['minutes']==0


def test_notebook_keeps_only_relevant_exact_excerpts_and_no_smalltalk(game):
    b,s=game
    reply='Здравствуйте. Я была в саду в шесть. Устала сегодня.'
    speech={'reply':reply,'account_ids':['s_time'],'excerpts':[{'account_id':'s_time','quote':'Я была в саду в шесть.'}],'emotion':'calm','attitude':'neutral'}
    s,_,_=world.reduce(b,s,[step('talk','n_ira')],P,{'0':speech})
    assert s['evidence'][0]['text']=='Я была в саду в шесть.'
    assert s['dialogue'][0]['reply']==reply
    speech.update(reply='Добрый вечер.',account_ids=[],excerpts=[],grounded=True,recordable=False)
    s,_,_=world.reduce(b,s,[step('talk','n_ira')],P,{'0':speech})
    assert len(s['evidence'])==1


@pytest.mark.parametrize('repaired',[True,False])
def test_bad_dialogue_is_rewritten_not_replaced_with_author_notes(client,game,repaired):
    client.post('/api/attempts/a1/commands',json={'kind':'talk','target':'n_ira','text':'Когда вы были в саду?','version':0},headers={'Idempotency-Key':'repair-dialogue-1'})
    class AI:
        case=db.one('SELECT * FROM cases WHERE id=?',('c1',))
        count=0
        def structured(self,category,prompt,context,schema):
            if category=='dialogue':
                self.count+=1
                if self.count==2:assert context['repair_feedback']
                text='Я была в саду в шесть.' if self.count==2 and repaired else 'Ирина была в саду в шесть.'
                return schema.model_validate({'reply':text,'account_ids':['s_time'],'emotion':'calm','attitude':'neutral'}).model_dump()
            return schema.model_validate({'grounded':True,'answers_question':True,'in_character':self.count==2 and repaired,'recordable':True,'reason':'Third-person self-reference','excerpts':[{'account_id':'s_time','quote':context['speech']['reply']}]}).model_dump()
    ai=AI();job=db.claim()
    if repaired:
        worker.command_job(job,ai)
        assert client.get('/api/attempts/a1').json()['world']['dialogue'][0]['reply']=='Я была в саду в шесть.'
    else:
        with pytest.raises(InvalidContent):worker.prepare_command(job,ai)
        current=client.get('/api/attempts/a1').json()['world']
        assert current['minute']==0 and current['evidence']==[] and current['dialogue']==[]
    assert ai.count==2


def test_repeat_search_can_recover_an_object_hidden_again(game):
    b,s=game
    b['checks'][0]['reveals_objects']=['o_key']
    s,_,_=world.reduce(b,s,[step('check','o_desk','f_lock')],P)
    s['objects']['o_key']['visible']=False
    assert not world.available_checks(b,s,'o_desk')[0]['done']
    after,result,_=world.reduce(b,s,[step('check','o_desk','f_lock')],P)
    assert after['objects']['o_key']['visible']
    assert result['minutes']==2 and result['evidence_ids']==[]


def test_dialogue_auditor_can_only_select_words_the_character_actually_said():
    from pydantic import ValidationError
    from app.worker import speech_audit_schema
    speech={'reply':'Я была в саду в шесть. Больше я ничего не видела.','account_ids':['s_time']}
    schema=speech_audit_schema(speech)
    verdict={'grounded':True,'answers_question':True,'in_character':True,'recordable':True,'reason':'Supported','excerpts':[{'account_id':'s_time','quote':'Я была в саду в шесть.'}]}
    assert schema.model_validate(verdict).excerpts[0].quote in speech['reply']
    verdict['excerpts'][0]['quote']='Я была в саду в семь.'
    with pytest.raises(ValidationError):schema.model_validate(verdict)
    verdict['excerpts'][0]={'account_id':'s_unrelated','quote':'Я была в саду в шесть.'}
    with pytest.raises(ValidationError):schema.model_validate(verdict)
