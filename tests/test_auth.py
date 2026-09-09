import time
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from app import config, db
from app import main


def configure_telegram(monkeypatch):
    monkeypatch.setattr(config,'TELEGRAM_CLIENT_ID','123456789')
    monkeypatch.setattr(config,'TELEGRAM_CLIENT_SECRET','test-secret')
    monkeypatch.setattr(config,'ORIGIN','http://testserver')
    monkeypatch.setattr(config,'PRODUCTION',False)


def begin_telegram_login(client):
    response=client.get('/api/auth/telegram/start',follow_redirects=False)
    assert response.status_code==303
    target=urlparse(response.headers['location'])
    assert target.scheme=='https'
    assert target.netloc=='oauth.telegram.org'
    params=parse_qs(target.query)
    assert params['client_id']==['123456789']
    assert params['redirect_uri']==['http://testserver/api/auth/telegram/callback']
    assert params['scope']==['openid profile']
    assert params['code_challenge_method']==['S256']
    assert len(params['code_challenge'][0])==43
    return params['state'][0]


def test_telegram_login_registers_once_and_creates_session(isolated,monkeypatch):
    configure_telegram(monkeypatch)
    monkeypatch.setattr(main,'exchange_telegram_code',lambda code,verifier:'id-token')
    monkeypatch.setattr(main,'verify_telegram_token',lambda token:{
        'telegram_id':'777000',
        'name':'Следователь',
        'username':'detective',
    })
    client=TestClient(main.app)

    state=begin_telegram_login(client)
    response=client.get(
        '/api/auth/telegram/callback',
        params={'state':state,'code':'first-code'},
        follow_redirects=False,
    )
    assert response.status_code==303
    assert response.headers['location']=='/#/library'
    assert client.get('/api/me').json()=={
        'email':None,
        'name':'Следователь',
        'username':'detective',
        'provider':'telegram',
        'admin':False,
    }

    state=begin_telegram_login(client)
    response=client.get(
        '/api/auth/telegram/callback',
        params={'state':state,'code':'second-code'},
        follow_redirects=False,
    )
    assert response.status_code==303
    assert db.one('SELECT count(*) AS count FROM users')['count']==1
    assert db.one('SELECT count(*) AS count FROM telegram_users')['count']==1
    assert db.one('SELECT count(*) AS count FROM sessions')['count']==2


def test_telegram_callback_rejects_wrong_state(isolated,monkeypatch):
    configure_telegram(monkeypatch)
    client=TestClient(main.app)
    begin_telegram_login(client)

    response=client.get(
        '/api/auth/telegram/callback',
        params={'state':'wrong-state','code':'code'},
        follow_redirects=False,
    )

    assert response.status_code==303
    assert response.headers['location']=='/?telegram_error=state#/login'
    assert db.one('SELECT count(*) AS count FROM users')['count']==0


def test_telegram_token_signature_and_claims_are_verified(monkeypatch):
    configure_telegram(monkeypatch)
    private_key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    public_key=private_key.public_key()
    monkeypatch.setattr(main,'PyJWKClient',lambda *args,**kwargs:SimpleNamespace(
        get_signing_key_from_jwt=lambda token:SimpleNamespace(key=public_key),
    ))
    now=int(time.time())
    claims={
        'iss':config.TELEGRAM_ISSUER,
        'aud':config.TELEGRAM_CLIENT_ID,
        'sub':'777000',
        'id':777000,
        'iat':now,
        'exp':now+300,
        'name':'Следователь',
        'preferred_username':'detective',
    }

    token=jwt.encode(claims,private_key,algorithm='RS256')
    assert main.verify_telegram_token(token)=={
        'telegram_id':'777000',
        'name':'Следователь',
        'username':'detective',
    }

    claims['aud']='another-client'
    wrong_audience=jwt.encode(claims,private_key,algorithm='RS256')
    with pytest.raises(jwt.InvalidAudienceError):
        main.verify_telegram_token(wrong_audience)
