"""Normalize a declared legacy schema, never change or silently skip old trades."""
import argparse
import json
import pathlib
import sys
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from hermes_lab.core import normalize,parse_file
from hermes_lab.journal import Journal

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--source',required=True);p.add_argument('--mapping',required=True);p.add_argument('--account',required=True);p.add_argument('--db',required=True);p.add_argument('--apply',action='store_true');a=p.parse_args()
    src=pathlib.Path(a.source).expanduser().resolve();dst=pathlib.Path(a.db).expanduser().resolve()
    if src==dst:raise SystemExit('Refus : source et base identiques')
    mapping=json.loads(pathlib.Path(a.mapping).read_text())
    events=normalize(parse_file(src.read_text(encoding='utf-8-sig'),src.name),mapping,a.account)
    if a.apply:result=Journal(dst).ingest(events,src.name)
    else:result={'validated_events':len(events),'db_written':False,'source_untouched':True}
    print(json.dumps(result,indent=2))
