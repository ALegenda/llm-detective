"""Reclaim discarded renders, retaining published art and resumable jobs."""
import contextlib
import json
import logging
import os
import shutil
import sqlite3
import time
from pathlib import Path
from . import config, db

log=logging.getLogger('detective.storage')
IMAGE_RESERVE=64*1024*1024


def disk_report():
    """Read-only size diagnostics that still run when database startup fails."""
    report={'at':time.time(),'files':{},'errors':[]}
    try:
        usage=shutil.disk_usage(config.DATA)
        report['filesystem']={'total':usage.total,'used':usage.used,'free':usage.free}
        fs=os.statvfs(config.DATA)
        report['inodes']={'total':fs.f_files,'free':fs.f_favail}
        images=[]
        for root,dirs,files in os.walk(config.DATA,followlinks=False):
            for name in files:
                path=Path(root)/name
                if path.is_symlink():continue
                info=path.stat()
                rel=path.relative_to(config.DATA)
                group=('assets' if rel.parts[0]=='assets' else name if len(rel.parts)==1 and name.startswith('detective.sqlite3') else 'other')
                item=report['files'].setdefault(group,{'count':0,'bytes':0,'allocated_bytes':0})
                item['count']+=1;item['bytes']+=info.st_size;item['allocated_bytes']+=info.st_blocks*512
                if group=='assets':images.append((str(rel),info.st_size,info.st_mtime))
        with contextlib.closing(sqlite3.connect(config.DB_PATH.as_uri()+'?mode=ro',uri=True,timeout=2)) as con:
            con.execute('PRAGMA query_only=ON')
            published={r[0] for r in con.execute('SELECT path FROM assets')}
            checkpoints={r[0] for r in con.execute("SELECT json_extract(checkpoint,'$.file') FROM jobs WHERE kind='asset' AND json_valid(checkpoint)") if r[0]}
            report['image_references']={}
            for path,size,mtime in images:
                group='published' if path in published else 'checkpoint' if path in checkpoints else 'unreferenced'
                item=report['image_references'].setdefault(group,{'count':0,'bytes':0,'older_than_hour_bytes':0})
                item['count']+=1;item['bytes']+=size
                if mtime<report['at']-3600:item['older_than_hour_bytes']+=size
            report['database_pages']={key:con.execute('PRAGMA '+key).fetchone()[0] for key in ('page_size','page_count','freelist_count')}
            try:
                report['database_tables']={name:size for name,size in con.execute('SELECT name,sum(pgsize) FROM dbstat GROUP BY name')}
            except sqlite3.Error as exc:report['errors'].append(type(exc).__name__+': dbstat unavailable')
    except (OSError,sqlite3.Error) as exc:
        report['errors'].append(type(exc).__name__+': '+str(exc))
    return report


def image_space_available(additional_bytes=0):
    # Image generation must leave room for saved progress and SQLite's journal.
    return shutil.disk_usage(config.DATA).free >= IMAGE_RESERVE+additional_bytes


def prune_discarded_images(min_age=3600):
    referenced={row['path'] for row in db.all_rows('SELECT path FROM assets')}
    for row in db.all_rows("SELECT checkpoint FROM jobs WHERE kind='asset' AND checkpoint IS NOT NULL"):
        checkpoint=json.loads(row['checkpoint'])
        if checkpoint.get('file'):referenced.add(checkpoint['file'])
    cutoff=time.time()-min_age
    count=0
    for path in (config.DATA/'assets').iterdir():
        # Generated UUID files only; do not touch backups or unknown user files.
        if path.is_symlink() or not path.is_file() or path.suffix not in {'.png','.part'}:continue
        if len(path.stem)!=32 or any(c not in '0123456789abcdef' for c in path.stem):continue
        if str(path.relative_to(config.DATA)) in referenced:continue
        try:
            if path.stat().st_mtime>=cutoff:continue
            path.unlink();count+=1
        except FileNotFoundError:pass
        except OSError:log.warning('Could not reclaim discarded image %s',path.name)
    if count:log.info('Reclaimed %s discarded image files',count)
    return count
