"""Local-only UI. No public hosting, credential fields or broker endpoints."""
import json
import logging
import secrets
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
from .core import RULES, ValidationError, example_rows, normalize, parse_file, metrics, replay, risk_estimate
from .journal import Journal

MAX_BODY=8*1024*1024


def make_handler(journal, token):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args): pass

        def reply(self,obj,status=200,kind="application/json; charset=utf-8"):
            data=obj if isinstance(obj,bytes) else json.dumps(obj,ensure_ascii=False,allow_nan=False).encode()
            self.send_response(status)
            self.send_header("Content-Type",kind)
            self.send_header("Content-Length",str(len(data)))
            self.send_header("Cache-Control","no-store")
            self.send_header("X-Content-Type-Options","nosniff")
            self.send_header("X-Frame-Options","DENY")
            self.send_header("Referrer-Policy","no-referrer")
            self.send_header("Content-Security-Policy","default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            self.end_headers();self.wfile.write(data)

        def trusted(self):
            port=self.server.server_port
            allowed={f"127.0.0.1:{port}",f"localhost:{port}"}
            if self.headers.get("Host") not in allowed:
                self.reply({"error":"Hôte refusé"},403);return False
            origin=self.headers.get("Origin")
            if origin and origin not in {f"http://{x}" for x in allowed}:
                self.reply({"error":"Origine refusée"},403);return False
            return True

        def do_GET(self):
            if not self.trusted():return
            route=urlsplit(self.path).path
            if route=="/":
                html=(Path(__file__).parent/"web"/"index.html").read_text(encoding="utf-8").replace("__SESSION_TOKEN__",token)
                return self.reply(html.encode(),kind="text/html; charset=utf-8")
            if self.headers.get("X-Lab-Token")!=token:
                return self.reply({"error":"Session requise"},403)
            if route=="/api/state":
                return self.reply({"version":"0.1.0","rules":RULES,"events":journal.events(),"notes":journal.notes(),"broker_connected":False})
            if route=="/api/export":
                return self.reply({"events":journal.events(),"notes":journal.notes(),"mode":"IMPORT_NON_RECONCILIE"})
            return self.reply({"error":"Route inconnue"},404)

        def do_POST(self):
            if not self.trusted():return
            if self.headers.get("X-Lab-Token")!=token:
                return self.reply({"error":"Session requise"},403)
            try:
                length=int(self.headers.get("Content-Length","0"))
                if length<=0 or length>MAX_BODY:
                    return self.reply({"error":"Corps vide ou supérieur à 8 Mo"},413)
                body=json.loads(self.rfile.read(length))
                if not isinstance(body,dict):raise ValidationError("Objet JSON requis")
                path=urlsplit(self.path).path
                if path=="/api/preview":
                    rows=parse_file(str(body.get("text","")),str(body.get("filename","")))
                    if not rows:raise ValidationError("Fichier vide")
                    if not isinstance(rows[0],dict):raise ValidationError("Ligne objet attendue")
                    return self.reply({"columns":list(rows[0]),"rows":len(rows),"preview":rows[:3]})
                if path=="/api/import":
                    events=normalize(parse_file(str(body.get("text","")),str(body.get("filename",""))),body.get("mapping"),str(body.get("account","")))
                    if any(e["account_id"]=="EXEMPLE_FICTIF" for e in events):
                        raise ValidationError("Exemple fictif réservé à l’aperçu ; utiliser un journal CLI distinct pour tester l’import.")
                    result=journal.ingest(events,body.get("filename","import"));return self.reply(result)
                if path=="/api/analyze":
                    sample=body.get("example") is True
                    events=normalize(example_rows()) if sample else journal.events()
                    profile=body.get("profile")
                    if not isinstance(profile,dict):raise ValidationError("Profil requis")
                    result={"example":sample,"events":events,"metrics":metrics(events,profile.get("initial_balance",profile.get("size",50000))),"replay":replay(events,profile)}
                    return self.reply(result)
                if path=="/api/risk":return self.reply(risk_estimate(body))
                if path=="/api/note":
                    journal.save_note(str(body.get("event_id","")),body.get("body"));return self.reply({"saved":True})
                return self.reply({"error":"Route inconnue"},404)
            except (ValidationError,ValueError,TypeError) as exc:
                self.reply({"error":str(exc)},400)
            except Exception:
                logging.exception("Erreur interne du laboratoire")
                self.reply({"error":"Erreur interne. Historique conservé ; consulter le terminal."},500)
    return Handler


def serve(db,port=8765,open_browser=True):
    journal=Journal(db)
    server=ThreadingHTTPServer(("127.0.0.1",port),make_handler(journal,secrets.token_urlsafe(32)))
    url=f"http://127.0.0.1:{server.server_port}"
    print(f"Hermes Prop Lab — {url}\nJournal : {journal.path.resolve()}\nLOCAL UNIQUEMENT • aucun ordre broker • Ctrl+C pour arrêter.",flush=True)
    if open_browser:threading.Timer(.5,lambda:webbrowser.open(url)).start()
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close()
