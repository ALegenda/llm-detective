import copy
import json
import pytest
from app import world
from app.generation import validate_blueprint
from conftest import step

P={'text':'Исследую','evidence':[],'target':''}


def test_blueprint_reachability(blueprint):
    assert validate_blueprint(blueprint)['title']
    b=copy.deepcopy(blueprint);b['objects'][1]['visible']=False
    with pytest.raises(ValueError,match='Unreachable'):validate_blueprint(b)


def test_invalid_graph_and_references(blueprint):
    b=copy.deepcopy(blueprint);b['locations'][0]['exits']=['missing']
    with pytest.raises(ValueError,match=r"l_hall\.exits.*missing"):validate_blueprint(b)
    b=copy.deepcopy(blueprint);b['truth']['culprits']=['n_missing']
    with pytest.raises(ValueError):validate_blueprint(b)


def test_one_sided_location_exit_is_canonicalized(blueprint):
    blueprint['locations'][1]['exits']=[]
    normalized=validate_blueprint(blueprint)
    assert normalized['locations'][0]['exits']==['l_garden']
    assert normalized['locations'][1]['exits']==['l_hall']
    assert blueprint['locations'][1]['exits']==[]


def test_disconnected_and_self_loop_locations_report_exact_ids(blueprint):
    blueprint['locations'][0]['exits']=['l_hall']
    blueprint['locations'][1]['exits']=[]
    with pytest.raises(ValueError) as error:
        validate_blueprint(blueprint)
    assert 'l_hall.exits contains itself' in str(error.value)
    assert "['l_garden'] are disconnected" in str(error.value)


def test_no_unwarned_arbitrary_escape(blueprint):
    b=copy.deepcopy(blueprint);b['reactions'][0].update(action='leave',trigger='time',warning='')
    with pytest.raises(ValueError,match='Unfair departure'):validate_blueprint(b)


def test_hidden_truth_and_knowledge_projection(game):
    b,s=game
    public=json.dumps(world.public_world(b,s),ensure_ascii=False)
    assert 'culprits' not in public and 'Лжёт' not in public and 'редкий цветок' not in public
    assert 'o_letter' not in public and 'n_lev' not in public
    ctx=json.dumps(world.interpreter_context(b,s,P),ensure_ascii=False)
    assert 'Ирина просила' not in ctx and 'requires_evidence' not in ctx


def test_general_inspection_not_all_clues(game):
    b,s=game;new,result,_=world.reduce(b,s,[step('look')],P)
    assert new['evidence']==[] and new['minute']==1
    assert s['minute']==0


def test_fixed_negative_experiment(game):
    b,s=game;s,r,_=world.reduce(b,s,[step('check','o_window','f_view')],P)
    assert 'не видно' in r['messages'][-1] and s['minute']==4
    assert s['evidence'][0]['kind']=='observation'


def test_inventory_open_state_and_rehydration(game):
    b,s=game
    s,r,_=world.reduce(b,s,[step('open','o_desk')],P)
    assert s['objects']['o_desk']['locked'] and s['minute']==0
    s,_,_=world.reduce(b,s,[step('take','o_key'),step('open','o_desk'),step('take','o_letter')],P)
    assert 'o_letter' in s['inventory'] and s['objects']['o_desk']['open']
    s=json.loads(json.dumps(s))
    assert s['objects']['o_letter']['location']=='inventory'
    s,_,_=world.reduce(b,s,[step('put','o_letter'),step('close','o_desk')],P)
    assert 'o_letter' not in s['inventory'] and s['objects']['o_letter']['location']=='l_hall'
    assert not s['objects']['o_desk']['open']


def test_cannot_teleport_or_take_remote(game):
    b,s=game
    s,r,_=world.reduce(b,s,[step('travel',destination='l_nonexistent')],P)
    assert s['location']=='l_hall' and s['minute']==0
    s,r,_=world.reduce(b,s,[step('take','o_letter')],P)
    assert s['inventory']==[]


def test_discovery_preserves_original_container_after_pickup(game):
    b,s=game
    blocked,_,_=world.reduce(b,s,[step('open','o_desk')],P)
    assert not any(e.get('discovery') for e in blocked['evidence'])
    s,_,_=world.reduce(b,s,[step('take','o_key'),step('open','o_desk')],P)
    found=[e for e in s['evidence'] if e.get('discovery')]
    assert len(found)==1
    assert found[0]['source']=='Письмо'
    assert '«Стол»' in found[0]['text'] and '«Кабинет»' in found[0]['text']
    assert 'перенести встречу' not in found[0]['text']
    s,_,_=world.reduce(b,s,[step('take','o_letter'),step('travel',destination='l_garden'),step('put','o_letter')],P)
    assert [e for e in s['evidence'] if e.get('discovery')]==found


def test_opening_check_records_discovery_without_revealing_nested_contents(game):
    b,s=game
    b['checks'][0]['opens_object']=True
    b['objects'].append(dict(b['objects'][2],id='o_hidden',name='Скрытое вложение',container='o_letter'))
    s=world.initial(b)
    s,_,_=world.reduce(b,s,[step('take','o_key'),step('check','o_desk','f_lock')],P)
    assert [e['source'] for e in s['evidence'] if e.get('discovery')]==['Письмо']
    s,_,_=world.reduce(b,s,[step('close','o_desk'),step('open','o_desk')],P)
    assert len([e for e in s['evidence'] if e.get('discovery')])==1


def test_compare_uses_only_existing_records_without_advancing_world(game):
    b,s=game
    world.add_evidence(s,'known_1','Первый осмотр','Три насечки.','observation','Диск')
    world.add_evidence(s,'known_2','Архивный контур','Три насечки и скол.','observation','Калька')
    s2,result,_=world.reduce(b,s,[step('compare')|{'evidence_ids':['known_1','known_2']}],P)
    assert s2==s and result['minutes']==0 and result['evidence_ids']==[]
    assert 'Три насечки.' in result['messages'][1]
    assert 'Три насечки и скол.' in result['messages'][2]
    for ids in [['known_1','f_letter'],['known_1','known_1']]:
        unchanged,rejected,_=world.reduce(b,s,[step('compare')|{'evidence_ids':ids}],P)
        assert unchanged==s and 'Уточните' in rejected['messages'][0]
        assert 'Ирина просила' not in str(rejected)


def test_cannot_present_unheld_item_or_unknown_fact(game):
    b,s=game
    with pytest.raises(ValueError):world.accessible_evidence(s,['o_key'])
    with pytest.raises(ValueError):world.accessible_evidence(s,['f_letter'])
    s,_,_=world.reduce(b,s,[step('take','o_key')],P)
    assert world.accessible_evidence(s,['o_key'])==['o_key']
    s,_,_=world.reduce(b,s,[step('put','o_key')],P)
    with pytest.raises(ValueError):world.accessible_evidence(s,['o_key'])


def test_knowledge_isolation_until_actually_shown(game):
    b,s=game
    world.add_evidence(s,'f_letter','Письмо','Дата встречи','observation','Письмо')
    assert 's_secret' not in [a['id'] for a in world.accounts_for(b,s,'n_ira',[])]
    assert 's_secret' in [a['id'] for a in world.accounts_for(b,s,'n_ira',['f_letter'])]
    ctx=world.speech_context(b,s,'n_ira',P)
    assert not ctx['shown'] and 'n_lev' not in json.dumps(ctx)


def test_dialogue_records_authorship_and_causal_movement(game):
    b,s=game;world.add_evidence(s,'f_letter','Письмо','Дата встречи','observation','Письмо')
    speech={'reply':'Да, я перенесла письмо.','account_ids':['s_secret'],'emotion':'anxious','attitude':'neutral'}
    s,r,_=world.reduce(b,s,[step('talk','n_ira')],P|{'evidence':['f_letter']},{'0':speech})
    assert s['people']['n_ira']['seen']==['f_letter']
    assert s['people']['n_lev']['seen']==[]
    assert s['evidence'][-1]['kind']=='statement'
    assert s['people']['n_ira']['emotion']=='anxious'
    assert s['people']['n_ira']['location']=='l_hall'
    s,r,_=world.reduce(b,s,[step('wait',minutes=2)],P)
    assert s['people']['n_ira']['location']=='l_garden'
    assert s['known_people']['n_ira']['location']=='l_garden'
    assert 'Ирина выходит в сад.' in r['messages']
    s,r,_=world.reduce(b,s,[step('wait',minutes=2)],P)
    assert 'Ирина выходит в сад.' not in r['messages']


def test_unavailable_secret_rejected(game):
    b,s=game
    with pytest.raises(ValueError,match='exceeds'):
        world.reduce(b,s,[step('talk','n_ira')],P,{'0':{'reply':'Секрет','account_ids':['s_secret'],'emotion':'calm','attitude':'neutral'}})


def test_reading_and_wall_clock_do_not_tick(game):
    b,s=game
    for _ in range(20):world.public_world(b,s)
    assert s['minute']==0 and s['triggered']=={}


def test_replay_same_truth_clean_state(game):
    b,s=game;before=copy.deepcopy(b)
    played,_,_=world.reduce(b,s,[step('take','o_key')],P)
    fresh=world.initial(b)
    assert fresh['inventory']==[] and played['inventory']==['o_key'] and b==before


def test_multiple_routes_and_comparison_prerequisites(game):
    b,s=game
    s,_,_=world.reduce(b,s,[step('take','o_key'),step('open','o_desk'),step('take','o_letter')],P)
    blocked,r,_=world.reduce(b,s,[step('check','o_letter','f_compare')],P)
    assert not blocked['evidence'][-1]['id']=='f_compare'
    for order in [['f_view','f_letter'],['f_letter','f_view']]:
        current=copy.deepcopy(s)
        for cid in order:
            c=next(c for c in b['checks'] if c['id']==cid)
            current,_,_=world.reduce(b,current,[step('check',c['object_id'],cid)],P)
        current,_,_=world.reduce(b,current,[step('check','o_letter','f_compare')],P)
        assert any(e['id']=='f_compare' for e in current['evidence'])


def test_unambiguous_container_location_is_canonicalized(blueprint):
    blueprint['objects'][2]['location']='o_desk'
    normalized=validate_blueprint(blueprint)
    assert normalized['objects'][2]['location']=='l_hall'
    assert blueprint['objects'][2]['location']=='o_desk'


@pytest.mark.parametrize('room_alias', ['l_safe', '', 'o_desk'])
def test_invalid_contained_room_uses_explicit_parent_chain(blueprint, room_alias):
    blueprint['objects'][2]['location']=room_alias
    normalized=validate_blueprint(blueprint)
    assert normalized['objects'][2]['location']=='l_hall'
    assert blueprint['objects'][2]['location']==room_alias


def test_nested_room_repair_is_independent_of_object_order(blueprint):
    blueprint['objects'].append(dict(blueprint['objects'][0],id='o_box',location='l_missing',container='o_desk',locked=False,key_id=''))
    blueprint['objects'][2].update(location='l_safe',container='o_box')
    for objects in [blueprint['objects'],list(reversed(blueprint['objects']))]:
        normalized=validate_blueprint(blueprint | {'objects':objects})
        assert all(o['location']=='l_hall' for o in normalized['objects'])


def test_container_repair_rejects_conflicting_real_rooms(blueprint):
    blueprint['objects'][2]['location']='l_garden'
    with pytest.raises(ValueError,match='o_letter.location=l_garden conflicts.*o_desk'):
        validate_blueprint(blueprint)


def test_container_cycles_are_rejected_even_without_essential_contents(blueprint):
    blueprint['objects'][1]['container']='o_window'
    blueprint['objects'][3]['container']='o_key'
    with pytest.raises(ValueError,match='Containment cycle: o_key -> o_window -> o_key'):
        validate_blueprint(blueprint)


def test_invalid_top_level_room_is_not_guessed(blueprint):
    blueprint['objects'][0]['location']='l_safe'
    with pytest.raises(ValueError,match='Object o_desk location must be a location id'):
        validate_blueprint(blueprint)


def test_room_id_in_check_reports_exact_repair(blueprint):
    blueprint['checks'][0]['object_id']='l_hall'
    with pytest.raises(ValueError, match=r"f_lock.object_id.*l_hall.*o_desk"):
        validate_blueprint(blueprint)


def test_tool_must_be_portable_and_check_not_self_required(blueprint):
    blueprint['checks'][0]['requires_tools']=['o_desk']
    blueprint['checks'][0]['requires_facts']=['f_lock']
    with pytest.raises(ValueError) as error: validate_blueprint(blueprint)
    assert 'f_lock.requires_tools' in str(error.value)
    assert 'requires itself' in str(error.value)


def test_contents_travel_with_container_and_closed_ancestor_hides_them(game):
    b,s=game
    b['objects'][0]['portable']=True
    s,_,_=world.reduce(b,s,[step('take','o_key'),step('open','o_desk'),step('take','o_desk'),step('travel',destination='l_garden')],P)
    assert world.effective_location('o_letter',s)=='inventory'
    assert world.visible(b['objects'][2],s)
    s,_,_=world.reduce(b,s,[step('close','o_desk')],P)
    assert not world.visible(b['objects'][2],s)
    s,_,_=world.reduce(b,s,[step('put','o_desk'),step('open','o_desk')],P)
    assert world.effective_location('o_letter',s)=='l_garden'
    assert world.visible(b['objects'][2],s)


def test_solid_object_cannot_open_and_container_cannot_contain_itself(game):
    b,s=game
    s,_,_=world.reduce(b,s,[step('open','o_key')],P)
    assert not s['objects']['o_key']['open'] and s['minute']==0
    b['objects'][0]['portable']=True
    s,_,_=world.reduce(b,s,[step('take','o_key'),step('open','o_desk'),step('put','o_desk',destination='o_desk')],P)
    assert s['objects']['o_desk']['container']==''


def test_notebook_quotes_delivered_speech_not_unspoken_authored_account(game):
    b,s=game
    reply='В шесть я гуляла среди деревьев.'
    speech={'reply':reply,'account_ids':['s_time'],'emotion':'calm','attitude':'neutral'}
    s,_,_=world.reduce(b,s,[step('talk','n_ira')],P,{'0':speech})
    assert s['evidence'][-1]['text']==reply
    assert s['dialogue'][-1]['reply']==reply


def test_opening_check_changes_container_and_cannot_bypass_lock(game):
    b,s=game
    b['checks'][0].update(opens_object=True)
    s,_,_=world.reduce(b,s,[step('check','o_desk','f_lock')],P)
    assert not s['objects']['o_desk']['open'] and s['minute']==0 and not s['evidence']
    s,_,_=world.reduce(b,s,[step('take','o_key'),step('check','o_desk','f_lock')],P)
    s=json.loads(json.dumps(s))
    assert s['objects']['o_desk']['open'] and not s['objects']['o_desk']['locked']
    assert world.visible(b['objects'][2],s)


def test_legacy_authored_opening_check_has_persistent_effect(game):
    b,s=game
    b['checks'][0]['intent']='Open the desk and inspect its interior'
    s,_,_=world.reduce(b,s,[step('take','o_key'),step('check','o_desk','f_lock')],P)
    assert s['objects']['o_desk']['open']
    normalized=validate_blueprint(b)
    assert normalized['checks'][0]['opens_object']


def test_briefing_introduces_only_publicly_named_people(blueprint):
    blueprint['briefing']={'objective':'Выяснить, куда исчезло письмо.','known_facts':['Письмо не найдено.','Следователь начинает в кабинете.'],'participants':[{'person_id':'n_lev','status':'witness','context':'Работал в саду в день исчезновения.'}]}
    b=validate_blueprint(blueprint);s=world.initial(b);world.observe_people(b,s)
    public=world.public_world(b,s)
    lev=next(n for n in public['people'] if n['id']=='n_lev')
    assert not lev['here'] and lev['last_seen']['location']==''
    briefing=world.public_briefing(b)
    assert briefing['participants'][0]['context']=='Работал в саду в день исчезновения.'
    assert 'knowledge' not in json.dumps(briefing) and 'culprits' not in json.dumps(briefing)
    assert s['minute']==0 and s['evidence']==[]


@pytest.mark.parametrize('ids', [['n_missing'],['n_ira','n_ira']])
def test_briefing_references_must_be_real_and_unique(blueprint,ids):
    blueprint['briefing']={'objective':'Найти письмо.','known_facts':['Письмо пропало.','Следователь в кабинете.'],'participants':[{'person_id':pid,'status':'contact','context':'Участник дела.'} for pid in ids]}
    with pytest.raises(ValueError,match='Briefing participants'):
        validate_blueprint(blueprint)


@pytest.mark.parametrize('field,value',[('reveals_objects',['o_missing']),('requires_tools',['o_missing']),('object_id','o_missing'),('requires_open','o_missing')])
def test_invalid_graph_references_report_validation_errors_before_traversal(blueprint,field,value):
    from app.generation import validate_blueprint
    blueprint['checks'][0][field]=value
    with pytest.raises(ValueError,match=field):validate_blueprint(blueprint)


def test_unreachable_clue_reports_causal_blockers(blueprint):
    blueprint['checks'][0]['requires_facts']=['f_compare']
    blueprint['checks'][2]['requires_facts']=['f_lock']
    with pytest.raises(ValueError) as error:validate_blueprint(blueprint)
    assert 'f_lock [missing facts: f_compare]' in str(error.value)
    assert 'f_compare [missing facts: f_view]' in str(error.value)
    assert 'f_view [missing facts: f_lock]' in str(error.value)


def test_speaker_cannot_bypass_disclosure_gates_through_private_author_memory(game):
    b,s=game
    person=world.index(b,'people')['n_ira']
    person['knowledge']=['AUTHOR_ONLY: I deliberately hid the letter for money.']
    for account in person['accounts']:
        account['private_context']='SECRET_REASON: This public denial conceals the theft.'
    secret=next(a for a in person['accounts'] if a['id']=='s_secret')
    secret['claim']='DISCLOSED_ONLY_AFTER_EVIDENCE: I moved the letter.'
    world.add_evidence(s,'f_letter','Письмо','Дата встречи','observation','Письмо')
    # Possession without showing it must not unlock a response.
    before=world.speech_context(b,s,'n_ira',P)
    text=json.dumps(before)
    assert 'AUTHOR_ONLY' not in text and 'SECRET_REASON' not in text
    assert 'DISCLOSED_ONLY_AFTER_EVIDENCE' not in text
    assert 'knowledge' not in before['person']
    after=world.speech_context(b,s,'n_ira',P|{'evidence':['f_letter']})
    text=json.dumps(after)
    assert 'DISCLOSED_ONLY_AFTER_EVIDENCE' in text
    assert 'AUTHOR_ONLY' not in text and 'SECRET_REASON' not in text
    assert all(set(a)=={'id','topic','claim','emotion'} for a in after['accounts'])
    assert person['knowledge'][0].startswith('AUTHOR_ONLY')  # Author truth is unchanged.
