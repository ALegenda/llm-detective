"""Consistent SQLite backup + only referenced private assets. Never run AI on restore."""
import argparse
import hashlib
import json
import shutil
import sqlite3
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app import config


def backup(source:Path,dest:Path):
    dest.mkdir(parents=True,exist_ok=False)
    with sqlite3.connect(source/'detective.sqlite3') as src, sqlite3.connect(dest/'detective.sqlite3') as out:
        src.backup(out)
        paths=[row[0] for row in out.execute('SELECT path FROM assets')]
    for relative in paths:
        target=dest/relative
        if not target.resolve().is_relative_to(dest.resolve()):raise ValueError('Invalid asset path')
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(source/relative,target)
    manifest={str(p.relative_to(dest)):hashlib.sha256(p.read_bytes()).hexdigest() for p in dest.rglob('*') if p.is_file()}
    (dest/'manifest.json').write_text(json.dumps(manifest,indent=2))
    return manifest


def restore(source:Path,dest:Path):
    manifest=json.loads((source/'manifest.json').read_text())
    for name,expected in manifest.items():
        path=source/name
        if not path.resolve().is_relative_to(source.resolve()) or hashlib.sha256(path.read_bytes()).hexdigest()!=expected:
            raise ValueError('Backup checksum failed')
    if dest.exists():raise ValueError('Restore destination must not exist')
    shutil.copytree(source,dest)
    with sqlite3.connect(dest/'detective.sqlite3') as con:
        if con.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('Database integrity check failed')
        if con.execute('PRAGMA user_version').fetchone()[0]>config.SCHEMA_VERSION:raise ValueError('Newer database schema')
        # Leased jobs are reclaimed; completed commands and assets stay completed.
        con.execute("UPDATE jobs SET lease_until=0 WHERE status='running'")
    return True


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('action',choices=['backup','restore'])
    parser.add_argument('destination',type=Path)
    parser.add_argument('--source',type=Path,default=config.DATA)
    args=parser.parse_args()
    result=globals()[args.action](args.source.resolve(),args.destination.resolve())
    print(args.action+' verified: '+str(args.destination.resolve()))
