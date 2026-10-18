import asyncio
import hashlib
import hmac
import json
import os
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit
from fastapi import FastAPI,Request,HTTPException
from fastapi.responses import HTMLResponse,JSONResponse
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from mcp.server.transport_security import TransportSecuritySettings

from hermes_lab.core import RULES,ValidationError,normalize,parse_file,metrics,replay,risk_estimate,example_rows
from hermes_lab.journal import Journal
from .connectors import Connectors,OPERATIONS
from .jobs import Jobs
from .settings import Settings


def has_secret_key(obj):
    if isinstance(obj,dict):return any(k.lower() in {'api_key','password','secret','token','authorization','access_token'} or has_secret_key(v) for k,v in obj.items())
    if isinstance(obj,list):return any(has_secret_key(v) for v in obj)
    return False


def create_app(settings=None):
    cfg=settings or Settings.load()
    journal=Journal(cfg.data/'journal.sqlite3')
    connectors=Connectors(cfg,journal)
    jobs=Jobs(cfg.data/'jobs.sqlite3',connectors.execute)
    mcp=FastMCP('Hermes Trading Connect',stateless_http=True,json_response=True,
                transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False))

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True,destructiveHint=False))
    def trading_report(size:int=50000,stage:str='evaluation',initial_balance:str='50000',initial_floor:str='') -> dict:
        """Read imported closed-trade metrics and research replay. No broker execution."""
        return connectors.execute('journal_report',{'profile':{'size':size,'stage':stage,'initial_balance':initial_balance,'initial_floor':initial_floor}})

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True,destructiveHint=False))
    def integrations_status() -> list:
        """Declared adapters and local sources; connection proofs are separate jobs."""
        return connectors.registry()

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True,destructiveHint=False))
    def investor_skills_list() -> list:return connectors.list_skills()

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True,destructiveHint=False))
    def investor_skill_read(name:str) -> dict:return connectors.read_document('investorskills',name)

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True,destructiveHint=False))
    def research_catalog_read() -> dict:return connectors.read_document('catalog')

    def submit(operation,payload,key):
        if operation not in OPERATIONS:raise ValueError('Opération inconnue')
        if not isinstance(payload,dict) or has_secret_key(payload):raise ValueError('Ne pas transmettre de secrets dans les tâches')
        return jobs.submit(operation,payload,key)

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False,destructiveHint=False))
    def research_submit(operation:str,payload:dict,request_key:str) -> dict:
        """Queue a bounded research/probe job. Can incur model/data costs when enabled. Never an order API."""
        return submit(operation,payload,request_key)

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True,destructiveHint=False))
    def research_result(job_id:str) -> dict:
        return jobs.get(job_id) or {'error':'not_found'}

    @asynccontextmanager
    async def lifespan(app):
        async with mcp.session_manager.run():yield
        jobs.close()

    app=FastAPI(title='Hermes Trading Connect',version='0.2.0',lifespan=lifespan,docs_url=None,redoc_url=None,openapi_url=None)
    app.state.journal=journal;app.state.connectors=connectors;app.state.jobs=jobs
    app.state.mcp=mcp;app.state.settings=cfg
    attempts={}

    def signature(value):return hmac.new(cfg.admin_token.encode(),value.encode(),hashlib.sha256).hexdigest()

    def cookie_ok(value):
        try:
            stamp,nonce,sig=value.split('.')
            return 0<=time.time()-int(stamp)<8*3600 and hmac.compare_digest(signature(stamp+'.'+nonce),sig)
        except (ValueError,AttributeError):return False

    def authorized(request):
        auth=request.headers.get('authorization','')
        return (auth.startswith('Bearer ') and hmac.compare_digest(auth[7:],cfg.admin_token)) or cookie_ok(request.cookies.get('prop_session',''))

    @app.middleware('http')
    async def guard(request,call_next):
        path=request.url.path
        if path=='/healthz':return JSONResponse({'status':'ok','version':'0.2.0','execution_enabled':False})
        try:
            hostname=urlsplit('http://'+request.headers.get('host','')).hostname
            hosts={'localhost','127.0.0.1'}|{s.strip() for s in os.environ.get('PROP_ALLOWED_HOSTS','').split(',') if s.strip()}
            if cfg.public_url:hosts.add(urlsplit(cfg.public_url).hostname)
            if hostname not in hosts:return JSONResponse({'error':'Hôte refusé'},status_code=403)
            origin=request.headers.get('origin')
            allowed={cfg.public_url,f'http://127.0.0.1:{request.url.port or 80}',f'http://localhost:{request.url.port or 80}'}
            if origin and origin not in allowed:return JSONResponse({'error':'Origine refusée'},status_code=403)
            length=int(request.headers.get('content-length','0'))
            if length>8*1024*1024:return JSONResponse({'error':'Requête trop grande'},status_code=413)
            public=path=='/auth/login' or (path=='/' and request.method=='GET')
            if not public and not authorized(request):return JSONResponse({'error':'Authentification requise'},status_code=401)
        except ValueError:return JSONResponse({'error':'Requête invalide'},status_code=400)
        response=await call_next(request)
        response.headers['Cache-Control']='no-store'
        response.headers['X-Content-Type-Options']='nosniff'
        response.headers['X-Frame-Options']='DENY'
        response.headers['Referrer-Policy']='no-referrer'
        response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        return response

    @app.exception_handler(ValidationError)
    async def validation_error(request,exc):return JSONResponse({'error':str(exc)},status_code=400)

    @app.exception_handler(ValueError)
    async def value_error(request,exc):return JSONResponse({'error':str(exc)},status_code=400)

    @app.post('/auth/login')
    async def login(request:Request):
        peer=request.client.host if request.client else 'unknown'
        ts=time.time();attempts[peer]=[x for x in attempts.get(peer,[]) if ts-x<60]
        if len(attempts[peer])>=5:return JSONResponse({'error':'Trop de tentatives. Attendre une minute.'},status_code=429)
        attempts[peer].append(ts)
        body=await request.json()
        supplied=body.get('token') if isinstance(body,dict) else ''
        if not isinstance(supplied,str) or not hmac.compare_digest(supplied,cfg.admin_token):return JSONResponse({'error':'Accès refusé'},status_code=401)
        value=str(int(ts))+'.'+secrets.token_hex(16)
        r=JSONResponse({'authenticated':True})
        r.set_cookie('prop_session',value+'.'+signature(value),max_age=8*3600,httponly=True,secure=cfg.public_url.startswith('https://'),samesite='strict')
        return r

    @app.post('/auth/logout')
    def logout():
        r=JSONResponse({'logged_out':True});r.delete_cookie('prop_session');return r

    @app.get('/')
    def index(request:Request):
        if not authorized(request):return HTMLResponse((cfg.root/'prop_connect/login.html').read_text())
        return HTMLResponse((cfg.root/'hermes_lab/web/index.html').read_text().replace('__SESSION_TOKEN__',''))

    @app.get('/api/state')
    def state():return {'version':'0.2.0','rules':RULES,'events':journal.events(),'notes':journal.notes(),'broker_connected':False}

    @app.get('/api/export')
    def export():return {'events':journal.events(),'notes':journal.notes(),'mode':'IMPORT_NON_RECONCILIE'}

    @app.post('/api/preview')
    async def preview(request:Request):
        b=await request.json();rows=parse_file(b['text'],b['filename'])
        if not rows or not isinstance(rows[0],dict):raise ValueError('Fichier vide ou invalide')
        return {'columns':list(rows[0]),'rows':len(rows),'preview':rows[:3]}

    @app.post('/api/import')
    async def ingest(request:Request):
        b=await request.json();events=normalize(parse_file(b['text'],b['filename']),b.get('mapping'),b.get('account',''))
        if any(e['account_id']=='EXEMPLE_FICTIF' for e in events):raise ValueError('Exemple fictif séparé du journal')
        return journal.ingest(events,b['filename'])

    @app.post('/api/analyze')
    async def analyze(request:Request):
        b=await request.json();profile=b.get('profile',{});example=b.get('example') is True
        events=normalize(example_rows()) if example else journal.events()
        return {'example':example,'events':events,'metrics':metrics(events,profile.get('initial_balance',profile.get('size',50000))),'replay':replay(events,profile)}

    @app.post('/api/risk')
    async def risk(request:Request):return risk_estimate(await request.json())

    @app.post('/api/note')
    async def note(request:Request):
        b=await request.json();journal.save_note(b['event_id'],b['body']);return {'saved':True}

    @app.get('/api/integrations')
    def integrations():return {'repositories':connectors.registry(),'skills':connectors.list_skills(),'jobs':jobs.list(),'operations':sorted(OPERATIONS),'execution_enabled':False,'remote_deployment_verified':False}

    @app.get('/api/jobs/{job_id}')
    def job_result(job_id:str):
        j=jobs.get(job_id)
        if not j:raise HTTPException(404,'not_found')
        return j

    @app.post('/api/jobs')
    async def queue_job(request:Request):
        b=await request.json();return submit(b.get('operation'),b.get('payload',{}),b.get('request_key',''))

    # Mounted after normal routes. The SDK owns /mcp and its protocol lifecycle.
    app.mount('/',mcp.streamable_http_app())
    return app
