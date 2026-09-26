import os
import time
import pytest
from app import config, db, storage, worker
from app.ai import InvalidContent, ProviderFailure
from test_reliability import queue_job


class ImageAI:
    def __init__(self):self.case=db.one("SELECT * FROM cases WHERE id='c1'")
    def image(self,*args,**kwargs):return b'controlled image bytes'
    def structured(self,*args,**kwargs):return {'accepted':True,'reason':'Valid'}


def test_disk_report_measures_files_without_reading_story_or_deleting(game):
    job=queue_job('asset',{'kind':'person','entity':'n_ira','variant':'base'})
    worker.asset_job(job,ImageAI())
    orphan=config.DATA/'assets'/('e'*32+'.png');orphan.write_bytes(b'orphan')
    report=storage.disk_report()
    assert report['files']['assets']['count']==2
    assert report['image_references']['unreferenced']['bytes']==6
    assert report['image_references']['published']['bytes']==len(b'controlled image bytes')
    assert report['database_pages']['page_size']>0
    assert orphan.read_bytes()==b'orphan'
    assert 'blueprint' not in str(report)


def test_disk_report_retains_file_sizes_when_database_cannot_open(game,monkeypatch):
    monkeypatch.setattr(config,'DB_PATH',config.DATA/'missing.sqlite3')
    report=storage.disk_report()
    assert report['filesystem']['total']>0
    assert report['files']['detective.sqlite3']['bytes']>0
    assert report['errors']


def test_cleanup_preserves_published_images_and_resumable_candidates(game):
    job=queue_job('asset',{'kind':'person','entity':'n_ira','variant':'base'})
    worker.asset_job(job,ImageAI())
    published=config.DATA/db.one('SELECT path FROM assets')['path']
    pending=config.DATA/'assets'/('b'*32+'.png');pending.write_bytes(b'pending')
    pending_job=queue_job('asset');db.save_checkpoint(pending_job,{'file':str(pending.relative_to(config.DATA))})
    orphan=config.DATA/'assets'/('c'*32+'.png');orphan.write_bytes(b'rejected')
    fresh=config.DATA/'assets'/('d'*32+'.png');fresh.write_bytes(b'writing')
    unknown=config.DATA/'assets'/'user-file.png';unknown.write_bytes(b'user')
    for path in [published,pending,orphan,unknown]:os.utime(path,(time.time()-7200,)*2)
    assert storage.prune_discarded_images()==1
    assert not orphan.exists()
    assert all(path.exists() for path in [published,pending,fresh,unknown])


def test_rejected_candidate_is_removed_after_feedback_checkpoint(game):
    job=queue_job('asset',{'kind':'object','entity':'o_key','variant':'base'})
    class RejectedAI(ImageAI):
        def structured(self,*args,**kwargs):return {'accepted':False,'reason':'Wrong object'}
    with pytest.raises(InvalidContent):worker.asset_job(job,RejectedAI())
    assert not list((config.DATA/'assets').glob('*.png'))
    assert 'feedback' in db.one('SELECT checkpoint FROM jobs')['checkpoint']


def test_disk_reserve_stops_art_before_provider_spend_but_keeps_progress(game,monkeypatch):
    monkeypatch.setattr(storage,'image_space_available',lambda:False)
    job=queue_job('asset',{'kind':'object','entity':'o_key','variant':'base'})
    class NoSpendAI(ImageAI):
        def image(self,*args,**kwargs):raise AssertionError('Must reserve space before spending')
    with pytest.raises(ProviderFailure,match='storage_full'):worker.asset_job(job,NoSpendAI())
    assert db.one("SELECT status FROM attempts WHERE id='a1'")['status']=='active'


def test_disk_filling_during_generation_does_not_consume_progress_reserve(game,monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(storage.shutil,'disk_usage',lambda path:SimpleNamespace(free=storage.IMAGE_RESERVE+10))
    job=queue_job('asset',{'kind':'object','entity':'o_key','variant':'base'})
    with pytest.raises(ProviderFailure,match='storage_full'):worker.asset_job(job,ImageAI())
    assert not list((config.DATA/'assets').iterdir())
    assert not db.all_rows('SELECT * FROM assets')
