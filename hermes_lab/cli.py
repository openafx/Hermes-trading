import argparse
import csv
import json
import sys
from pathlib import Path
from .core import ValidationError, example_rows, normalize, parse_file, metrics, replay
from .journal import Journal


def main():
    p=argparse.ArgumentParser(description="Hermes Prop Lab 0.1 - recherche locale, aucun ordre broker")
    p.add_argument("--db",default="data/journal.sqlite3")
    sub=p.add_subparsers(dest="cmd",required=True)
    serve=sub.add_parser("serve");serve.add_argument("--port",type=int,default=8765);serve.add_argument("--no-browser",action="store_true")
    imp=sub.add_parser("import");imp.add_argument("file");imp.add_argument("--mapping");imp.add_argument("--account",default="")
    rep=sub.add_parser("report");rep.add_argument("--size",type=int,default=50000);rep.add_argument("--initial-balance",type=str);rep.add_argument("--initial-floor",type=str);rep.add_argument("--stage",choices=["evaluation","funded"],default="evaluation");rep.add_argument("--daily-loss-limit");rep.add_argument("--output")
    exp=sub.add_parser("export");exp.add_argument("output")
    sample=sub.add_parser("example");sample.add_argument("output")
    back=sub.add_parser("backup");back.add_argument("output")
    a=p.parse_args()
    try:
        if a.cmd=="serve":
            from .server import serve
            return serve(a.db,a.port,not a.no_browser)
        if a.cmd=="example":
            with open(a.output,"x",newline="",encoding="utf-8") as f:
                rows=example_rows();w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
            print("Exemple FICTIF écrit ; aucun résultat réel.")
            return
        journal=Journal(a.db)
        if a.cmd=="import":
            f=Path(a.file)
            mapping=json.loads(Path(a.mapping).read_text()) if a.mapping else {}
            result=journal.ingest(normalize(parse_file(f.read_text(encoding="utf-8-sig"),f.name),mapping,a.account),f.name)
        elif a.cmd=="report":
            profile={"size":a.size,"stage":a.stage,"initial_balance":a.initial_balance or a.size,"initial_floor":a.initial_floor,"daily_loss_limit":a.daily_loss_limit}
            events=journal.events()
            result={"mode":"HISTORIQUE_IMPORTE_NON_RECONCILIE","account_verified":False,"metrics":metrics(events,profile["initial_balance"]),"replay":replay(events,profile)}
        elif a.cmd=="export":
            Path(a.output).write_text(json.dumps(journal.events(),ensure_ascii=False,indent=2),encoding="utf-8")
            result={"export":a.output}
        else:
            journal.backup(a.output);result={"backup":a.output}
        text=json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)
        if a.cmd=="report" and a.output:Path(a.output).write_text(text,encoding="utf-8")
        else:print(text)
    except (ValidationError,OSError,ValueError) as exc:
        print(f"Erreur : {exc}",file=sys.stderr);raise SystemExit(2)
