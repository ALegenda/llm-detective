import copy
import json
import random
import pytest
from pydantic import ValidationError
from app import db, worker, world
from app.ai import InvalidContent, ProviderFailure
from app.story_pipeline import (validate_outline,world_schema,script_schema,compile_world,exercise_world,build)
from test_reliability import queue_job


@pytest.fixture
def outline():
    return {
        'title':'Письмо перед отплытием','subtitle':'Портовая история','setting_rules':'Реализм','visual_style':'Editorial ink','start_time':'09:00',
        'public_incident':'Исчезло письмо. Три свидетеля ждут в порту.', 'missing_item_name':'Источник 6','missing_item_clue_index':6, 'event':'Исчезновение письма', 'culprit_indices':[0],
        'method':'Письмо переложено в коробку','motive':'Скрыть перенос встречи','timeline':['08:00 письмо получено','08:10 встреча перенесена','08:20 письмо скрыто','08:30 обнаружена пропажа'],
        'explanation':'Ирина скрыла письмо с новым временем встречи.','dramatic_question':'Почему письмо исчезло?','fair_reversal':'Опоздание оказалось намеренным.',
        'places':[{'name':name,'description':name,'atmosphere':'Туман','image_prompt':'Empty architecture','travel_minutes':2} for name in ['Контора','Причал','Мастерская']],
        'cast':[{'name':name,'occupation':'Смотритель','appearance':'Взрослый человек','personality':'Сдержанный','interests':'Работа','location_index':i,'knowledge':['Встреча в 18:00.'],'innocent_secret':''} for i,name in enumerate(['Ирина','Лев','Анна'])],
        'clues':[{'source_name':'Источник '+str(i),'source_surface':'Закрытый документ '+str(i),'source_image_prompt':'Closed paper','location_index':i%3,'portable':i==6,'container_path':['Сейф'] if i==0 else ['Коробка','Чехол'] if i==6 else [],'source_kind':'artifact' if i==6 else 'document','method':'compare' if i==3 else 'read','focus':'запись '+str(i),'observation':'На документе '+str(i)+' указана встреча в 18:00.','significance':'Устанавливает время'} for i in range(7)],
        'conclusions':[{'aspect':['identity','method','motive'][i],'description':'Критерий '+str(i),'clue_indices':[i,i+1]} for i in range(3)]}


def recipe(**changes):
    return {'container_index':None,'tool_name':'','tool_surface':'','tool_image_prompt':'','tool_location':'','requires':[],'minutes':2}|changes


def box(name,location,parent=None,locked=False):
    return {'name':name,'surface':'Закрытая ёмкость','image_prompt':'Closed container','location':location,'parent_index':parent,'locked':locked,'key_name':name+' ключ' if locked else '', 'key_surface':'Ключ' if locked else '', 'key_image_prompt':'Key' if locked else '', 'key_location':'l_3' if locked else ''}


def make_plan():
    plan={f'f_{i+1}':recipe() for i in range(7)}
    plan['containers']=[box('Сейф','l_1',locked=True),box('Коробка','l_1'),box('Чехол','l_1',parent=1)]
    plan['f_1']=recipe(container_index=0)
    plan['f_4']=recipe(requires=['f_1','f_3'],tool_name='Лупа',tool_surface='Лупа',tool_image_prompt='Lens',tool_location='l_2')
    plan['f_7']=recipe(container_index=2)
    return plan


def make_script():
    s={'introduction':'Письмо исчезло из конторы. Выясните обстоятельства.','objective':'Найти письмо и объяснить исчезновение.','known_facts':['Письмо исчезло.','В конторе три свидетеля.'],'hints':['Осмотрите контору.','Сравните время.','Сопоставьте документы.']}
    for i in range(3):
        s[f'n_{i+1}']={'public_context':'Работает в порту.','status':'witness','accounts':[{'topic':str(j),'claim':'Я видела письмо.','private_context':'Вспоминает письмо','requires_evidence':['f_1'] if j==2 else [],'emotion':'calm'} for j in range(3)]}
    s.update({f'f_{i+1}':{'result':'На документе '+str(i)+' указана встреча в 18:00.'} for i in range(7)})
    return s


SETTINGS={'theme':'Портовая история','language':'ru','duration':'short','difficulty':'medium'}


@pytest.mark.parametrize('label',['Подменённый образец К-17','Бумажная метка на подменённом образце','Настоящий образец','Counterfeit sample'])
def test_public_source_name_cannot_announce_the_answer(outline,label):
    outline['clues'][1]['source_name']=label
    with pytest.raises(ValueError,match='hidden conclusion'):validate_outline(outline,SETTINGS)


def test_comparison_can_reuse_original_object_without_a_fictitious_report(outline):
    original=outline['clues'][0]
    for key in ['source_name','source_surface','source_image_prompt','location_index','portable','container_path','source_kind']:
        outline['clues'][3][key]=copy.deepcopy(original[key])
    plan=make_plan();plan['f_4']['container_index']=0
    b=compile_world(validate_outline(outline,SETTINGS),plan,make_script())
    assert b['checks'][0]['object_id']==b['checks'][3]['object_id']
    assert len([o for o in b['objects'] if o['name']==original['source_name']])==1
    exercise_world(b,reverse=False)


def test_two_tests_of_one_object_do_not_become_independent_proof(outline):
    for key in ['source_name','source_surface','source_image_prompt','location_index','portable','container_path','source_kind']:
        outline['clues'][3][key]=copy.deepcopy(outline['clues'][0][key])
    outline['conclusions'][0]['clue_indices']=[0,3]
    with pytest.raises(ValueError,match='two independent physical sources'):validate_outline(outline,SETTINGS)


def test_reading_only_case_fails_activity_requirement(outline):
    outline['clues'][3]['method']='read'
    with pytest.raises(ValueError,match='player comparisons'):validate_outline(outline,SETTINGS)


def test_opening_badges_do_not_single_out_the_hidden_culprit(outline):
    script=make_script();script['n_1']['status']='person_of_interest'
    b=compile_world(validate_outline(outline,SETTINGS),make_plan(),script)
    assert {p['status'] for p in b['briefing']['participants']}=={'contact'}


def test_compiler_replay_obtains_all_clues_and_recovers_item_in_both_orders(outline):
    b=compile_world(validate_outline(outline,SETTINGS),make_plan(),make_script())
    for reverse in [False,True]:
        proof=exercise_world(b,['o_7'],reverse)
        assert set(c['id'] for c in b['checks'])<=set(proof['acquired'])
        assert proof['recovered']==['o_7']
        assert any(s['kind']=='open' and s['target']=='o_box_1' for s in proof['actions'])
    assert b['objects'][0]['container']=='' # compiler-created key never inside its safe
    assert all(c['opens_object'] is False and c['reveals_objects']==[] for c in b['checks'])


def test_schema_prevents_forward_dependencies_unknown_rooms_and_unknown_dialogue_evidence(outline):
    plan=make_plan();plan['f_1']['requires']=['f_7']
    with pytest.raises(ValidationError):world_schema(outline).model_validate(plan)
    plan=make_plan();plan['f_2']['requires']=['f_1']
    with pytest.raises(ValidationError):world_schema(outline).model_validate(plan)
    plan=make_plan();plan['f_4']['requires']=['f_7']
    with pytest.raises(ValidationError):world_schema(outline).model_validate(plan)
    plan=make_plan();plan['containers'][0]['key_location']='l_unknown'
    with pytest.raises(ValidationError):world_schema(outline).model_validate(plan)
    s=make_script();s['n_1']['accounts'][0]['requires_evidence']=['s_unavailable']
    with pytest.raises(ValidationError):script_schema(outline).model_validate(s)


def test_outline_requires_independent_existing_material_support(outline):
    outline['conclusions'][0]['clue_indices']=[0,0]
    with pytest.raises(ValueError,match='distinct'):validate_outline(outline,SETTINGS)


def test_certificate_detects_broken_runtime_state_instead_of_trusting_graph(outline):
    b=compile_world(outline,make_plan())
    key=next(o for o in b['objects'] if o['id']=='o_key_1')
    key.update(container='o_box_1',location='l_1',visible=False)
    with pytest.raises(ValueError,match='cannot obtain'):exercise_world(b,['o_7'])


def test_compiled_recipe_combinations_are_executable(outline):
    rng=random.Random(271828)
    for _ in range(35):
        plan={}
        for i in range(7):
            ci=i%3 if rng.random()<.7 else None
            outline['clues'][i]['container_path']=[f'Контейнер {ci}'] if ci is not None else []
            plan[f'f_{i+1}']=recipe(container_index=ci,requires=['f_1','f_3'] if i==3 else [])
        plan['containers']=[box(f'Контейнер {i}',f'l_{i+1}',locked=rng.random()<.5) for i in range(3)]
        b=compile_world(outline,plan)
        assert exercise_world(b,['o_7'],bool(rng.randrange(2)))['recovered']==['o_7']


class FakeAuthor:
    def __init__(self,outline):
        self.case=db.one("SELECT * FROM cases WHERE id='c1'")
        self.outline=outline;self.calls=[];self.contexts={};self.fail_once=None;self.audit_issues=[]
    def structured(self,category,prompt,context,schema):
        self.calls.append(category);self.contexts[category]=context
        if self.fail_once==category:
            self.fail_once=None
            raise ProviderFailure('provider_connection_unknown',True)
        result={'story_outline':self.outline,'story_world':make_plan(),'story_script':make_script(),
                'story_reader':{'culprits':['n_1'],'method':'Переложено','motive':'Скрыть время','reasoning':'Документы сходятся','supporting_evidence':['f_1','f_2'],'unresolved_ambiguities':[]},
                'story_fact_audit':{'issues':[],'strengths':[]},
                'story_audit':{'issues':self.audit_issues,'strengths':['Материальные маршруты']},'story_adjudication':{'blocking_issue_indices':list(range(len(self.audit_issues))),'reasoning':'Verified'}}[category]
        return schema.model_validate(result).model_dump()


def test_pipeline_reader_never_receives_truth_and_resume_reuses_finished_stages(game,outline):
    j=queue_job('generate');ai=FakeAuthor(outline);ai.fail_once='story_script'
    with pytest.raises(ProviderFailure):build(j,ai,SETTINGS)
    j=db.one('SELECT * FROM jobs WHERE id=?',(j['id'],))
    b,review=build(j,ai,SETTINGS)
    assert ai.calls.count('story_outline')==1 and ai.calls.count('story_world')==1
    reader=ai.contexts['story_reader']
    assert not any(key in reader for key in ['truth','outline','compiled_world'])
    assert 'private_context' not in json.dumps(reader)
    assert 'knowledge' not in json.dumps(reader)
    assert 'testimony' not in reader
    assert review['mechanical_proof']['orders_tested']==2
    assert b['truth']['culprits']==['n_1']


def test_script_repair_preserves_outline_world_and_mechanical_certificate(game,outline):
    j=queue_job('generate');ai=FakeAuthor(outline)
    ai.audit_issues=[{'stage':'script','target':'introduction','contradiction':'Unfinished sentence','correction':'Complete the sentence'}]
    with pytest.raises(InvalidContent):build(j,ai,SETTINGS)
    j=db.one('SELECT * FROM jobs WHERE id=?',(j['id'],));cp=json.loads(j['checkpoint'])
    assert all(key in cp for key in ['outline','world','certificate'])
    assert not any(key in cp for key in ['script','reader','blueprint','audit'])
    ai.audit_issues=[]
    build(j,ai,SETTINGS)
    assert ai.calls.count('story_outline')==1 and ai.calls.count('story_world')==1
    assert ai.calls.count('story_script')==2
    assert ai.contexts['story_script']['repair_feedback']


def test_worker_publishes_only_certified_new_pipeline_and_keeps_proof_private(client,game,outline):
    with db.transaction() as con:con.execute("UPDATE cases SET status='writing',blueprint=NULL WHERE id='c1'")
    j=queue_job('generate');ai=FakeAuthor(outline)
    worker.generate(j,ai)
    row=db.one("SELECT * FROM cases WHERE id='c1'")
    assert row['status']=='ready'
    assert json.loads(row['blueprint'])['_meta']['generation_version']==6
    assert json.loads(row['review'])['mechanical_proof']['clues_acquired']==7
    public=client.get('/api/cases/c1').json()
    assert 'certificate' not in public and 'outline' not in public and 'truth' not in public
    assert public['generation_version']==6 and public['stage']=='ready'


def test_wrong_independent_solution_cannot_publish_even_if_auditor_misses_it(game,outline):
    j=queue_job('generate');ai=FakeAuthor(outline);original=ai.structured
    def wrong(category,prompt,context,schema):
        result=original(category,prompt,context,schema)
        if category=='story_reader':result['culprits']=['n_2']
        return result
    ai.structured=wrong
    with pytest.raises(InvalidContent,match='Independent reader'):build(j,ai,SETTINGS)
    cp=json.loads(db.one('SELECT checkpoint FROM jobs WHERE id=?',(j['id'],))['checkpoint'])
    assert cp['stage']=='outline' and 'blueprint' not in cp


@pytest.mark.parametrize('revision,stage_rejections,retry',[(1,1,True),(2,2,True),(3,3,False),(4,1,True),(5,1,False)])
def test_new_pipeline_repair_budget_is_per_stage_and_globally_bounded(game,revision,stage_rejections,retry):
    j=queue_job('generate')
    db.save_checkpoint(j,{'pipeline_version':2,'revision':revision,'stage':'world','rejections':{'world':stage_rejections}})
    worker.fail_job(j,InvalidContent('test'))
    assert db.one('SELECT status FROM jobs WHERE id=?',(j['id'],))['status']==('retry' if retry else 'failed')


def test_unfounded_audit_objection_does_not_rewrite_a_playable_story(game,outline):
    j=queue_job('generate');ai=FakeAuthor(outline)
    ai.audit_issues=[{'stage':'outline','target':'container','contradiction':'Item in container and room','correction':'Add a redundant sentence'}]
    original=ai.structured
    def adjudicate(category,prompt,context,schema):
        if category=='story_adjudication':return {'blocking_issue_indices':[],'reasoning':'Containment is transitive, no conflicting facts.'}
        return original(category,prompt,context,schema)
    ai.structured=adjudicate
    _,review=build(j,ai,SETTINGS)
    assert review['accepted']
    assert ai.calls.count('story_outline')==1


def test_nested_containers_reveal_the_real_artifact_only_after_both_open(outline):
    b=compile_world(outline,make_plan(),make_script());s=world.initial(b)
    artifact=world.index(b,'objects')['o_7']
    pouch=world.index(b,'objects')['o_box_3']
    assert not world.visible(artifact,s) and not world.visible(pouch,s)
    from conftest import step
    s,_,_=world.reduce(b,s,[step('open','o_box_2')],{'evidence':[],'text':''})
    assert world.visible(pouch,s) and not world.visible(artifact,s)
    s,_,_=world.reduce(b,s,[step('open','o_box_3')],{'evidence':[],'text':''})
    assert world.visible(artifact,s)
    s,_,_=world.reduce(b,s,[step('take','o_7')],{'evidence':[],'text':''})
    assert s['objects']['o_7']['location']=='inventory'
    assert all(c['intent'].startswith(('Осмотреть:','Прочитать:','Сопоставить:','Провести проверку:')) for c in b['checks'])


def test_experiment_without_instrument_and_comparison_without_inputs_are_rejected(outline):
    plan=make_plan();outline['clues'][1]['method']='experiment'
    with pytest.raises(ValueError):compile_world(outline,plan)
    outline['clues'][1]['method']='read';plan['f_4']['requires']=[]
    with pytest.raises(ValueError):compile_world(outline,plan)


def test_container_cannot_replace_the_actual_missing_item(outline):
    outline['clues'][6]['source_name']='Чехол с письмом'
    with pytest.raises(ValueError,match='own portable artifact'):validate_outline(outline,SETTINGS)


def test_two_experiments_share_the_same_physical_tool(outline):
    plan=make_plan()
    for i in [4,5]:
        outline['clues'][i]['method']='experiment'
        plan[f'f_{i+1}']=recipe(tool_name='Лупа',tool_surface='Лупа',tool_image_prompt='Lens',tool_location='l_2')
    b=compile_world(outline,plan)
    tools=[o for o in b['objects'] if o['name']=='Лупа']
    assert len(tools)==1
    assert b['checks'][4]['requires_tools']==b['checks'][5]['requires_tools']==[tools[0]['id']]
    assert exercise_world(b,['o_7'])['recovered']==['o_7']


def test_shared_container_and_exterior_fixture_use_one_physical_object(outline):
    plan=make_plan();plan['containers']=[box('Коробка','l_1'),box('Коробка','l_1')]
    plan['f_1']['container_index']=0;plan['f_7']['container_index']=1
    outline['clues'][0]['container_path']=['Коробка']
    outline['clues'][6]['container_path']=['Коробка']
    outline['clues'][0].update(source_name='Коробка',source_kind='fixture',method='inspect')
    b=compile_world(outline,plan)
    assert len([o for o in b['objects'] if o['name']=='Коробка'])==1
    assert b['checks'][0]['object_id']==b['checks'][6]['requires_open']=='o_box_1'
    assert exercise_world(b,['o_7'])['recovered']==['o_7']


def test_manual_retry_resets_repair_budget_but_preserves_revision_and_stages(client,game):
    j=queue_job('generate')
    db.save_checkpoint(j,{'pipeline_version':3,'revision':8,'repair_round_failures':5,'stage':'world','rejections':{'world':3},'outline':{'kept':True}})
    with db.transaction() as con:con.execute("UPDATE jobs SET status='failed' WHERE id=?",(j['id'],))
    assert client.post('/api/jobs/'+j['id']+'/retry').status_code==200
    cp=json.loads(db.one('SELECT checkpoint FROM jobs WHERE id=?',(j['id'],))['checkpoint'])
    assert cp['revision']==8 and cp['outline']=={'kept':True}
    assert cp['rejections']=={} and cp['repair_round_failures']==0


def test_layout_cannot_hide_exposed_trace_or_expose_authored_hidden_artifact(outline):
    outline['clues'][1].update(source_name='След на полу',source_kind='trace',container_path=[])
    plan=make_plan();plan['f_2']['container_index']=0
    assert next(o for o in compile_world(outline,plan)['objects'] if o['id']=='o_2')['container']==''
    for wrong in [None,0,1,99]:
        plan=make_plan();plan['f_7']['container_index']=wrong
        b=compile_world(outline,plan)
        assert next(o for o in b['objects'] if o['id']=='o_7')['container']=='o_box_3'
        assert exercise_world(b,['o_7'])['recovered']==['o_7']


def test_outline_rejects_one_container_in_two_physical_places(outline):
    outline['clues'][1]['container_path']=['Сейф']
    with pytest.raises(ValueError,match='conflicting locations'):validate_outline(outline,SETTINGS)


def test_fact_audit_is_scoped_to_observation_inputs_and_repaired_with_script(game,outline):
    j=queue_job('generate');ai=FakeAuthor(outline);original=ai.structured
    def audit(category,prompt,context,schema):
        if category=='story_fact_audit':
            assert 'knowledge' not in json.dumps(context)
            assert context['observations'][0]['prior_observations']==[]
            assert len(context['observations'][3]['prior_observations'])==2
            return {'issues':[{'stage':'script','target':'f_1','contradiction':'Local inspection compares an unavailable source','correction':'Keep only local marks'}],'strengths':[]}
        if category=='story_adjudication':return {'blocking_issue_indices':[0],'reasoning':'Two exact conflicting facts verified'}
        return original(category,prompt,context,schema)
    ai.structured=audit
    with pytest.raises(InvalidContent,match='script'):build(j,ai,SETTINGS)
    cp=json.loads(db.one('SELECT checkpoint FROM jobs WHERE id=?',(j['id'],))['checkpoint'])
    assert 'outline' in cp and 'world' in cp and 'certificate' in cp
    assert 'script' not in cp and 'fact_audit' not in cp


@pytest.mark.parametrize('field',['name','occupation','appearance'])
def test_public_cast_fields_cannot_disclose_guilt_before_play(outline,field):
    outline['cast'][0][field]='Реставратор; виновник кражи'
    with pytest.raises(ValueError,match='guilt label'):validate_outline(outline,SETTINGS)
