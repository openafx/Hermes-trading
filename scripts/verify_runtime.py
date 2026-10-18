"""Probe installed modules/MCP without LLM calls or broker access."""
import argparse,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import httpx
p=argparse.ArgumentParser();p.add_argument('--url',default='http://127.0.0.1:8765');a=p.parse_args()
import os,time,uuid
token=os.environ.get('PROP_ADMIN_TOKEN')
if not token:raise SystemExit('Set PROP_ADMIN_TOKEN in the process environment')
out={}
with httpx.Client(base_url=a.url,headers={'Authorization':'Bearer '+token},timeout=30,follow_redirects=False) as c:
    for name in ['openbb','vibe','quantstats','vectorbt','pybroker','nautilus','tradingagents']:
        response=c.post('/api/jobs',json={'operation':'connection_probe','payload':{'integration':name},'request_key':str(uuid.uuid4())});response.raise_for_status();job=response.json()
        for _ in range(320):
            job=c.get('/api/jobs/'+job['id']).json()
            if job['status'] not in {'queued','running'}:break
            time.sleep(1)
        out[name]={'job_id':job['id'],'status':job['status'],'result':job['result'],'error':job['error']}
print(json.dumps(out,indent=2,ensure_ascii=False))
raise SystemExit(0 if all(x['status']=='succeeded' for x in out.values()) else 1)
