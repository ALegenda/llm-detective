"""Reclaim discarded renders, retaining published art and resumable jobs."""
import json
import logging
import shutil
import time
from . import config, db

log=logging.getLogger('detective.storage')
IMAGE_RESERVE=64*1024*1024


def image_space_available():
    # Image generation must leave room for saved progress and SQLite's journal.
    return shutil.disk_usage(config.DATA).free >= IMAGE_RESERVE


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
