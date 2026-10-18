"""Retrieve locked upstream sources without running them or overwriting work."""
import argparse
import concurrent.futures
import json
import os
import pathlib
import re
import shutil
import subprocess


def fetch_one(entry, root):
    key=entry['id'];dest=root/key;revision=entry.get('revision','')
    if not re.fullmatch('[a-f0-9]{40}',revision):return {'id':key,'status':'missing_pin'}
    repo=entry.get('canonical_repo',entry['repo'])
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+',repo):raise ValueError('Invalid repository')
    if dest.exists():
        try:
            sha=subprocess.check_output(['git','-C',str(dest),'rev-parse','HEAD'],text=True,stderr=subprocess.DEVNULL).strip()
            return {'id':key,'status':'already_present' if sha==revision else 'conflict_preserved','revision':sha}
        except subprocess.CalledProcessError:return {'id':key,'status':'existing_directory_preserved'}
    root.mkdir(parents=True,exist_ok=True)
    temp=root/(key+'.fetching')
    if temp.exists():return {'id':key,'status':'unfinished_directory_preserved'}
    temp.mkdir()
    env={**os.environ,'GIT_TERMINAL_PROMPT':'0'}
    try:
        subprocess.run(['git','init','-q',str(temp)],check=True,env=env,capture_output=True)
        subprocess.run(['git','-C',str(temp),'remote','add','origin','https://github.com/'+repo+'.git'],check=True,env=env,capture_output=True)
        subprocess.run(['git','-C',str(temp),'fetch','--depth','1','--filter=blob:none','origin',revision],check=True,env=env,capture_output=True,timeout=180)
        subprocess.run(['git','-C',str(temp),'checkout','--detach','FETCH_HEAD'],check=True,env=env,capture_output=True,timeout=180)
        got=subprocess.check_output(['git','-C',str(temp),'rev-parse','HEAD'],text=True,env=env).strip()
        if got!=revision:raise ValueError('Revision mismatch')
        temp.rename(dest)
        return {'id':key,'status':'source_present','revision':got,'executed':False}
    except Exception as exc:
        # Only the new, privately-created staging directory is removed.
        shutil.rmtree(temp)
        return {'id':key,'status':'fetch_failed','error_type':type(exc).__name__}


def main():
    p=argparse.ArgumentParser();p.add_argument('--destination',default='vendor');p.add_argument('--lock',default=str(pathlib.Path(__file__).resolve().parents[1]/'repos.lock.json'));p.add_argument('--only',nargs='*');a=p.parse_args()
    data=json.loads(pathlib.Path(a.lock).read_text());rows=data['repositories']
    if a.only:rows=[x for x in rows if x['id'] in a.only]
    root=pathlib.Path(a.destination).resolve()
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        results=list(pool.map(lambda r:fetch_one(r,root),rows))
    print(json.dumps(results,indent=2))
    if any(r['status'] not in {'source_present','already_present'} for r in results):raise SystemExit(2)

if __name__=='__main__':main()
