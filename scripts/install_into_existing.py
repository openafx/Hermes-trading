"""Additive installer. Never modifies an existing application's entrypoint/state."""
import argparse
import hashlib
import json
import pathlib
import shutil
import tempfile


def install(source,target,apply=False):
    source=pathlib.Path(source).resolve();target=pathlib.Path(target).expanduser().resolve()
    if not target.is_dir():raise ValueError('Le dossier Hermes-trading doit déjà exister ; aucune installation existante créée artificiellement.')
    destination=target/'extensions'/'prop-connect-v0.2'
    if destination.exists():raise ValueError('Extension déjà présente : conservée, aucune écriture')
    plan={'existing_project':str(target),'destination':str(destination),'apply':apply,'state_preserved':True,'existing_entrypoint_modified':False,'hermes_config_modified':False,'railway_deployed':False}
    if not apply:return plan
    destination.parent.mkdir(exist_ok=True)
    staging=pathlib.Path(tempfile.mkdtemp(prefix='prop-connect-install-',dir=destination.parent))
    try:
        for item in source.iterdir():
            if item.name in {'data','vendor','.venv','__pycache__'}:continue
            out=staging/item.name
            if item.is_dir():shutil.copytree(item,out,ignore=shutil.ignore_patterns('__pycache__','*.pyc','.git'))
            elif item.is_file():shutil.copy2(item,out)
        (staging/'INSTALLATION.json').write_text(json.dumps(plan,indent=2))
        staging.rename(destination)
    except Exception:
        shutil.rmtree(staging);raise
    return plan


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--target',required=True);p.add_argument('--apply',action='store_true');a=p.parse_args()
    try:print(json.dumps(install(pathlib.Path(__file__).resolve().parents[1],a.target,a.apply),indent=2))
    except ValueError as e:p.error(str(e))
