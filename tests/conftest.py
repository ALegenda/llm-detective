import copy
import json
import time
import pytest
from fastapi.testclient import TestClient
from app import config, db, world
from app.main import app


@pytest.fixture
def isolated(tmp_path,monkeypatch):
    monkeypatch.setattr(config,'DATA',tmp_path)
    monkeypatch.setattr(config,'DB_PATH',tmp_path/'detective.sqlite3')
    db.init()
    yield tmp_path


@pytest.fixture
def blueprint():
    return {'title':'Контрольное дело','subtitle':'Исчезновение письма','introduction':'Письмо пропало. Исследуйте кабинет.','setting_rules':'Реалистичный мир.','visual_style':'Editorial green ink illustration','start_location':'l_hall','start_time':'09:00',
    'locations':[{'id':'l_hall','name':'Кабинет','description':'Светлая комната с окном.','atmosphere':'Тихо','image_prompt':'Empty architecture','exits':['l_garden'],'travel_minutes':2},{'id':'l_garden','name':'Сад','description':'Сад с дорожкой.','atmosphere':'Ветер','image_prompt':'Empty garden','exits':['l_hall'],'travel_minutes':3}],
    'objects':[
    {'id':'o_desk','name':'Стол','location':'l_hall','surface':'Стол с ящиком.','image_prompt':'Closed desk','portable':False,'visible':True,'container':'','locked':True,'key_id':'o_key'},
    {'id':'o_key','name':'Ключ','location':'l_hall','surface':'Латунный ключ.','image_prompt':'Brass key','portable':True,'visible':True,'container':'','locked':False,'key_id':''},
    {'id':'o_letter','name':'Письмо','location':'l_hall','surface':'Сложенное письмо.','image_prompt':'Folded paper','portable':True,'visible':False,'container':'o_desk','locked':False,'key_id':''},
    {'id':'o_window','name':'Окно','location':'l_hall','surface':'Окно в сад.','image_prompt':'Window','portable':False,'visible':True,'container':'','locked':False,'key_id':''}],
    'checks':[
    {'id':'f_lock','object_id':'o_desk','intent':'Осмотреть замок на следы','result':'Следов взлома нет.','requires_facts':[],'requires_tools':[],'requires_open':'','reveals_objects':[],'minutes':2,'essential':True},
    {'id':'f_letter','object_id':'o_letter','intent':'Прочитать письмо','result':'Ирина просила перенести встречу на 18:00.','requires_facts':[],'requires_tools':[],'requires_open':'o_desk','reveals_objects':[],'minutes':3,'essential':True},
    {'id':'f_view','object_id':'o_window','intent':'Проверить видимость входа в сад','result':'Вход скрыт стеной, из окна его не видно.','requires_facts':[],'requires_tools':[],'requires_open':'','reveals_objects':[],'minutes':4,'essential':True},
    {'id':'f_compare','object_id':'o_letter','intent':'Сравнить время в письме с наблюдениями','result':'Время встречи не совпадает с показанием.','requires_facts':['f_letter','f_view'],'requires_tools':[],'requires_open':'','reveals_objects':[],'minutes':5,'essential':True}],
    'people':[
    {'id':'n_ira','name':'Ирина','role':'Секретарь','appearance':'Рыжая женщина в зелёном жакете','personality':'Сдержанная','interests':'Сохранить работу','location':'l_hall','knowledge':['Она перенесла письмо.'],
     'accounts':[{'id':'s_time','topic':'Встреча','claim':'Я была в саду в шесть.','private_context':'Лжёт о времени','requires_evidence':[],'emotion':'calm'},{'id':'s_secret','topic':'Письмо','claim':'Да, я перенесла письмо.','private_context':'Говорит о своём поступке','requires_evidence':['f_letter'],'emotion':'anxious'}]},
    {'id':'n_lev','name':'Лев','role':'Садовник','appearance':'Мужчина в синей рубашке','personality':'Спокойный','interests':'Сад','location':'l_garden','knowledge':['Уходил поливать цветы.'],'accounts':[{'id':'s_garden','topic':'Работа','claim':'Я поливал цветы.','private_context':'Правда','requires_evidence':[],'emotion':'calm'}]}],
    'reactions':[{'id':'r_move','actor':'n_ira','trigger':'evidence','trigger_id':'f_letter','delay':3,'action':'move','destination':'l_garden','object_id':'','recipient':'','warning':'Ирина собирается выйти в сад.','observed':'Ирина выходит в сад.','consequence':''}],
    'truth':{'event':'Исчезновение письма','culprits':['n_ira'],'motive':'Скрыть перенос встречи','method':'Письмо переложено в ящик','timeline':['17:00 письмо перенесено'],'innocent_secrets':['Лев тайно выращивает редкий цветок.'],'explanation':'Ирина переложила письмо.','criteria':[{'description':'Перенос письма','evidence_ids':['f_letter','f_compare']},{'description':'Без взлома','evidence_ids':['f_lock','f_view']}]},'hints':['Исследуйте видимые предметы.','Сравните время и линию обзора.']}


def step(kind,target='',check_id='',destination='',minutes=0):
    return {'kind':kind,'target':target,'check_id':check_id,'destination':destination,'topic':'','minutes':minutes,'explanation':''}


@pytest.fixture
def game(isolated,blueprint):
    b=copy.deepcopy(blueprint);s=world.initial(b);world.observe_people(b,s);now=time.time()
    with db.transaction() as con:
        con.execute('INSERT INTO users VALUES(?,?,?,?)',('u1','u1@test.invalid','unused',now))
        con.execute('INSERT INTO cases(id,user_id,request_key,request_hash,settings,status,blueprint,created,updated) VALUES(?,?,?,?,?,?,?,?,?)',('c1','u1','initial-key','hash',db.encode({'theme':'Музей','difficulty':'medium','duration':'short','language':'ru'}),'ready',db.encode(b),now,now))
        con.execute('INSERT INTO attempts(id,case_id,user_id,state,initial_state,created,updated) VALUES(?,?,?,?,?,?,?)',('a1','c1','u1',db.encode(s),db.encode(s),now,now))
    return b,s


@pytest.fixture
def client(game):
    from app.main import authenticate
    app.dependency_overrides[authenticate]=lambda: {'id':'u1','email':'u1@test.invalid'}
    c=TestClient(app,raise_server_exceptions=True,headers={'X-Requested-With':'detective'})
    yield c
    c.close()
    app.dependency_overrides.clear()
