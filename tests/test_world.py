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
    with pytest.raises(ValueError):validate_blueprint(b)
    b=copy.deepcopy(blueprint);b['truth']['culprits']=['n_missing']
    with pytest.raises(ValueError):validate_blueprint(b)


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
