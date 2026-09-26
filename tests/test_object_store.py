import hashlib
import io
import json
import pytest
from botocore.exceptions import ClientError
from app import config, db, object_store, storage, worker
from test_reliability import queue_job
from test_storage import ImageAI


class FakeS3:
    def __init__(self):self.objects={};self.content_types={};self.uploads=0;self.corrupt=False;self.gets=0
    def head_object(self,**kw):
        if kw['Key'] not in self.objects:
            raise ClientError({'Error':{'Code':'404'}},'HeadObject')
        return {'ContentLength':len(self.objects[kw['Key']])}
    def put_object(self,**kw):
        assert kw['StorageClass']=='STANDARD'
        assert kw['IfNoneMatch']=='*'
        assert kw['Metadata']['sha256']==hashlib.sha256(kw['Body']).hexdigest()
        self.objects[kw['Key']]=kw['Body'];self.uploads+=1
        self.content_types[kw['Key']]=kw['ContentType']
    def get_object(self,**kw):
        self.gets+=1
        return {'Body':io.BytesIO(b'corrupt' if self.corrupt else self.objects[kw['Key']])}
    def delete_object(self,**kw):self.objects.pop(kw['Key'],None)
    def get_paginator(self,operation):return self
    def paginate(self,**kw):
        items=[{'Key':key,'Size':len(data)} for key,data in self.objects.items()]
        yield {'Contents':items[:1]}
        yield {'Contents':items[1:]}


@pytest.fixture
def remote(monkeypatch):
    fake=FakeS3()
    monkeypatch.setattr(config,'ASSET_STORAGE','r2')
    monkeypatch.setattr(config,'R2_MIGRATE_LOCAL',True)
    monkeypatch.setattr(object_store,'client',lambda:fake)
    return fake


def test_migration_verifies_and_removes_only_images_preserving_database(game,remote):
    key='assets/'+ 'a'*32+'.png'
    path=config.DATA/key;path.write_bytes(b'image one')
    db_before=config.DB_PATH.read_bytes()
    object_store.migrate_local()
    assert remote.objects[key]==b'image one'
    assert not path.exists()
    assert config.DB_PATH.read_bytes()==db_before
    assert db.one("SELECT status FROM attempts WHERE id='a1'")['status']=='active'
    object_store.migrate_local()
    assert remote.uploads==1


def test_failed_verification_keeps_local_copy_and_can_resume(game,remote):
    key='assets/'+ 'b'*32+'.png'
    path=config.DATA/key;path.write_bytes(b'image two')
    remote.corrupt=True
    with pytest.raises(RuntimeError,match='verification failed'):object_store.migrate_local()
    assert path.read_bytes()==b'image two'
    remote.corrupt=False
    object_store.migrate_local()
    assert not path.exists()
    assert remote.uploads==1


def test_existing_different_object_is_not_overwritten(game,remote):
    key='assets/'+ 'c'*32+'.png'
    path=config.DATA/key;path.write_bytes(b'local')
    remote.objects[key]=b'different remote'
    with pytest.raises(RuntimeError):object_store.migrate_local()
    assert remote.objects[key]==b'different remote'
    assert path.read_bytes()==b'local'
    assert remote.uploads==0


def test_migration_requires_explicit_flag_and_rejects_unknown_files(game,remote,monkeypatch):
    path=config.DATA/'assets'/('d'*32+'.png');path.write_bytes(b'local')
    monkeypatch.setattr(config,'R2_MIGRATE_LOCAL',False)
    with pytest.raises(RuntimeError,match='explicitly enable'):object_store.migrate_local()
    monkeypatch.setattr(config,'R2_MIGRATE_LOCAL',True)
    unknown=config.DATA/'assets'/'user-file.txt';unknown.write_bytes(b'keep')
    with pytest.raises(ValueError):object_store.migrate_local()
    assert path.exists() and unknown.exists() and remote.uploads==0


def test_new_renders_and_identity_reference_use_r2_without_local_files(game,remote):
    base_job=queue_job('asset',{'kind':'person','entity':'n_ira','variant':'base'})
    worker.asset_job(base_job,ImageAI())
    base=db.one('SELECT * FROM assets')
    class EditAI(ImageAI):
        def image(self,prompt,reference=None,**kwargs):
            assert reference==b'controlled image bytes'
            return b'edited portrait'
        def structured(self,*args,**kwargs):
            assert kwargs['images']==[b'controlled image bytes',b'edited portrait']
            return {'accepted':True,'reason':'Valid'}
    edit_job=queue_job('asset',{'kind':'person','entity':'n_ira','variant':'anxious'})
    worker.asset_job(edit_job,EditAI())
    assert storage.read_image(base['path'])==b'controlled image bytes'
    assert len(remote.objects)==2
    assert not list((config.DATA/'assets').iterdir())


def test_rejected_remote_render_is_deleted_after_feedback(game,remote):
    class RejectAI(ImageAI):
        def structured(self,*args,**kwargs):return {'accepted':False,'reason':'Bad picture'}
    job=queue_job('asset',{'kind':'object','entity':'o_key','variant':'base'})
    from app.ai import InvalidContent
    with pytest.raises(InvalidContent):worker.asset_job(job,RejectAI())
    assert remote.objects=={}
    assert not list((config.DATA/'assets').iterdir())


def test_remote_image_route_checks_player_discovery_before_read(client,remote):
    key='assets/'+ 'e'*32+'.png'
    remote.objects[key]=b'private illustration'
    with db.transaction() as con:
        con.execute("INSERT INTO assets(id,case_id,kind,entity_id,variant,path,provenance,created) VALUES('art','c1','object','o_letter','base',?,'{}',0)",(key,))
    response=client.get('/api/assets/art')
    assert response.status_code==404 and remote.gets==0
    with db.transaction() as con:
        con.execute("UPDATE assets SET entity_id='o_key'")
    response=client.get('/api/assets/art')
    assert response.status_code==200 and response.content==b'private illustration'
    assert response.headers['cache-control'].startswith('private')
    assert not list((config.DATA/'assets').iterdir())


def test_bucket_cap_includes_paginated_objects(game,remote,monkeypatch):
    remote.objects={'one':b'123','two':b'456'}
    monkeypatch.setattr(config,'R2_MAX_BYTES',8)
    with pytest.raises(OSError,match='cap'):storage.write_image('assets/'+'f'*32+'.png',b'789')
    assert remote.uploads==0
