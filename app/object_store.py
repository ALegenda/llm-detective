"""Private S3 image storage and verified, resumable disk evacuation."""
import contextlib
import hashlib
import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import lru_cache
import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from . import config, imaging

_writes=threading.Lock()


@lru_cache(maxsize=1)
def client():
    return boto3.client('s3',endpoint_url=config.R2_ENDPOINT_URL,
        aws_access_key_id=config.R2_ACCESS_KEY_ID,aws_secret_access_key=config.R2_SECRET_ACCESS_KEY,
        region_name='auto',config=Config(signature_version='s3v4',connect_timeout=10,read_timeout=45,
            retries={'max_attempts':3,'mode':'standard'},s3={'addressing_style':'path'},
            request_checksum_calculation='when_required',response_checksum_validation='when_required'))


def key(path):
    if not re.fullmatch(r'assets/[a-f0-9]{32}\.(png|webp|part)',path):
        raise ValueError('Invalid image storage key')
    return path


def read(path):
    response=client().get_object(Bucket=config.R2_BUCKET,Key=key(path))
    with contextlib.closing(response['Body']) as body:
        return body.read()


def inventory():
    count=size=0
    for page in client().get_paginator('list_objects_v2').paginate(Bucket=config.R2_BUCKET):
        for item in page.get('Contents',[]):count+=1;size+=item['Size']
    return {'count':count,'bytes':size}


def ensure_uploaded(path,data):
    """Never replace an existing different object, and verify actual remote bytes."""
    path=key(path);sha=hashlib.sha256(data).hexdigest()
    try:
        existing=client().head_object(Bucket=config.R2_BUCKET,Key=path)
    except ClientError as exc:
        if str(exc.response.get('Error',{}).get('Code')) not in {'404','NoSuchKey','NotFound'}:raise
        existing=None
    if existing is None:
        client().put_object(Bucket=config.R2_BUCKET,Key=path,Body=data,
            ContentType=imaging.path_media_type(path),StorageClass='STANDARD',Metadata={'sha256':sha},IfNoneMatch='*')
    remote=read(path)
    if len(remote)!=len(data) or hashlib.sha256(remote).hexdigest()!=sha:
        raise RuntimeError('Remote image verification failed; local copy retained')


def write(path,data):
    # Dedicated bucket, single service writer. Leave 1 GB below the free storage
    # allowance. This is an application storage cap, not a Cloudflare billing cap.
    with _writes:
        if inventory()['bytes']+len(data)>config.R2_MAX_BYTES:
            raise OSError('R2 application storage cap reached')
        ensure_uploaded(path,data)


def delete(path):
    client().delete_object(Bucket=config.R2_BUCKET,Key=key(path))


def migrate_local():
    """Run before SQLite/workers, including when the persistent disk is full."""
    if config.ASSET_STORAGE!='r2':return
    folder=config.DATA/'assets'
    paths=sorted(folder.iterdir())
    if not paths:return
    if not config.R2_MIGRATE_LOCAL:
        raise RuntimeError('Local images remain; explicitly enable R2_MIGRATE_LOCAL before switching')
    for path in paths:
        if path.is_symlink() or not path.is_file():raise RuntimeError('Unknown entry in image directory; migration stopped')
        key(str(path.relative_to(config.DATA)))
    before=inventory()
    local_bytes=sum(p.stat().st_size for p in paths)
    if before['bytes']+local_bytes>config.R2_MAX_BYTES:
        raise RuntimeError('Migration could exceed the application R2 storage cap')
    print('R2_MIGRATION '+json.dumps({'phase':'start','files':len(paths),'bytes':local_bytes}),flush=True)
    def transfer(path):
        original=path.stat()
        data=path.read_bytes()
        ensure_uploaded(str(path.relative_to(config.DATA)),data)
        current=path.stat()
        if (current.st_size,current.st_mtime_ns)!=(original.st_size,original.st_mtime_ns):
            raise RuntimeError('Local image changed during migration; copy retained')
        path.unlink()
        return len(data)
    # Client is initialized on the main thread before concurrent use.
    client()
    count=size=0
    with ThreadPoolExecutor(max_workers=4) as pool:
        for future in as_completed([pool.submit(transfer,p) for p in paths]):
            size+=future.result();count+=1
            if count%25==0:print('R2_MIGRATION '+json.dumps({'phase':'progress','verified_removed':count,'bytes':size}),flush=True)
    print('R2_MIGRATION '+json.dumps({'phase':'complete','verified_removed':count,'bytes':size,'local_remaining':len(list(folder.iterdir())),'remote':inventory()}),flush=True)
