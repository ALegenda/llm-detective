import base64
import io
from types import SimpleNamespace

import pytest
from PIL import Image
from app import config, db, imaging, storage, worker
from app.ai import AI, InvalidContent
from app.models import VisualReview
from test_reliability import queue_job
from test_storage import ImageAI
from test_object_store import remote


@pytest.fixture(autouse=True)
def isolated_provider(monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY','test-only-no-network')
    monkeypatch.setattr(config,'IMAGE_FORMAT','webp')
    monkeypatch.setattr(config,'IMAGE_QUALITY','low')
    monkeypatch.setattr(config,'IMAGE_COMPRESSION',80)


def picture(fmt='WEBP',size=(832,832)):
    output=io.BytesIO()
    Image.new('RGB',size,(70,110,90)).save(output,format=fmt)
    return output.getvalue()


@pytest.mark.parametrize('landscape,size',[(False,(832,832)),(True,(1152,768))])
def test_real_adapter_requests_compact_webp_and_records_usage(game,monkeypatch,landscape,size):
    monkeypatch.setattr(config,'IMAGE_MODEL','gpt-image-2.5-flare')
    monkeypatch.setattr(config,'IMAGE_SQUARE_SIZE','832x832')
    monkeypatch.setattr(config,'IMAGE_LANDSCAPE_SIZE','1152x768')
    data=picture(size=size)
    ai=AI(queue_job('asset'))
    def generate(**kwargs):
        assert kwargs=={'model':'gpt-image-2.5-flare','prompt':'Test scene',
            'size':f'{size[0]}x{size[1]}','quality':'low','output_format':'webp','output_compression':80}
        return SimpleNamespace(data=[SimpleNamespace(b64_json=base64.b64encode(data).decode())],
            usage=SimpleNamespace(input_tokens=12,output_tokens=120))
    monkeypatch.setattr(ai.client.images,'generate',generate)
    assert ai.image('Test scene',landscape=landscape)==data
    operation=db.one('SELECT * FROM operations')
    assert operation['model']=='gpt-image-2.5-flare'
    assert operation['output_tokens']==120


@pytest.mark.parametrize('fmt',['PNG','WEBP'])
def test_edit_and_visual_review_use_actual_reference_mime(game,monkeypatch,fmt):
    data=picture(fmt)
    ai=AI(queue_job('asset'))
    def edit(**kwargs):
        assert kwargs['image']==('reference.'+fmt.lower(),data,'image/'+fmt.lower())
        return SimpleNamespace(data=[SimpleNamespace(b64_json=base64.b64encode(picture()).decode())])
    monkeypatch.setattr(ai.client.images,'edit',edit)
    ai.image('Change expression',reference=data)
    def parse(**kwargs):
        assert kwargs['input'][0]['content'][1]['image_url'].startswith('data:image/'+fmt.lower()+';base64,')
        return SimpleNamespace(output_parsed=VisualReview(accepted=True,reason='Valid'))
    monkeypatch.setattr(ai.client.responses,'parse',parse)
    ai.structured('visual_review','Review',{},VisualReview,images=[data])


def test_png_generation_rollback_omits_compression_and_rejects_corrupt_output(game,monkeypatch):
    ai=AI(queue_job('asset'))
    monkeypatch.setattr(config,'IMAGE_FORMAT','png')
    def generate(**kwargs):
        assert 'output_compression' not in kwargs
        return SimpleNamespace(data=[SimpleNamespace(b64_json=base64.b64encode(picture('PNG')).decode())])
    monkeypatch.setattr(ai.client.images,'generate',generate)
    assert imaging.extension(ai.image('Test'))=='png'
    monkeypatch.setattr(ai.client.images,'generate',lambda **kwargs:SimpleNamespace(data=[SimpleNamespace(b64_json=base64.b64encode(b'bad image').decode())]))
    with pytest.raises(InvalidContent,match='validation'):ai.image('Test')


def test_webp_roundtrip_private_route_and_legacy_checkpoint(client,remote):
    data=picture()
    class WebPAI(ImageAI):
        def image(self,*args,**kwargs):return data
    job=queue_job('asset',{'kind':'object','entity':'o_key','variant':'base'})
    worker.asset_job(job,WebPAI())
    asset=db.one('SELECT * FROM assets')
    assert asset['path'].endswith('.webp')
    assert remote.content_types[asset['path']]=='image/webp'
    response=client.get('/api/assets/'+asset['id'])
    assert response.status_code==200 and response.content==data
    assert response.headers['content-type']=='image/webp'
    assert not list((config.DATA/'assets').iterdir())
    # A render generated before deployment must resume without a new AI call.
    key='assets/'+'9'*32+'.png'
    old=picture('PNG');remote.objects[key]=old
    pending=queue_job('asset',{'kind':'person','entity':'n_ira','variant':'base'})
    db.save_checkpoint(pending,{'file':key})
    pending=db.one('SELECT * FROM jobs WHERE id=?',(pending['id'],))
    class ResumeAI(ImageAI):
        def image(self,*args,**kwargs):raise AssertionError('Do not redraw a checkpoint')
        def structured(self,*args,**kwargs):
            assert kwargs['images']==[old]
            return {'accepted':True,'reason':'Valid'}
    worker.asset_job(pending,ResumeAI())
    legacy=db.one("SELECT * FROM assets WHERE kind='person'")
    response=client.get('/api/assets/'+legacy['id'])
    assert response.status_code==200 and response.headers['content-type']=='image/png'
    assert response.content==old


def test_rejected_webp_is_deleted_from_r2(game,remote):
    class RejectedAI(ImageAI):
        def image(self,*args,**kwargs):return picture()
        def structured(self,*args,**kwargs):return {'accepted':False,'reason':'Wrong object'}
    with pytest.raises(InvalidContent):
        worker.asset_job(queue_job('asset',{'kind':'object','entity':'o_key','variant':'base'}),RejectedAI())
    assert remote.objects=={}


def test_local_cleanup_recognizes_webp_but_preserves_active_checkpoint(game):
    kept=config.DATA/'assets'/('8'*32+'.webp');kept.write_bytes(picture())
    discarded=config.DATA/'assets'/('7'*32+'.webp');discarded.write_bytes(picture())
    db.save_checkpoint(queue_job('asset'),{'file':str(kept.relative_to(config.DATA))})
    assert storage.prune_discarded_images(min_age=-1)==1
    assert kept.exists() and not discarded.exists()


@pytest.mark.parametrize('size',['auto','512x512','831x832','4096x1024','1024x128','832x832x832'])
def test_unsupported_sizes_fail_before_starting_workers(isolated,monkeypatch,size):
    monkeypatch.setattr(config,'IMAGE_SQUARE_SIZE',size)
    with pytest.raises(RuntimeError,match='dimensions'):config.preflight()
