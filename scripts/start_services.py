"""Supervise private MCP services and the public authenticated gateway."""
import json,os,signal,subprocess,sys,time
from pathlib import Path

root=Path(__file__).resolve().parents[1];envroot=Path(os.environ.get('PROP_ENVS_DIR','/opt/prop-envs'));children=[]
manifest=envroot/'interpreters.json'
if manifest.exists():
    for k,v in json.loads(manifest.read_text()).items():os.environ.setdefault(k,v)
os.environ.setdefault('PROP_BIND','0.0.0.0')
def stop(*_):
    for child in children:
        if child.poll() is None:child.terminate()
    for child in children:
        try:child.wait(timeout=10)
        except subprocess.TimeoutExpired:child.kill()
    raise SystemExit(0)
signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
try:
    for name,port,exe,args in [('openbb',8901,'openbb-mcp',['--enable-cli-tools','false']),('vibe',8902,'vibe-trading-mcp',['--transport','http'])]:
        binary=envroot/name/'bin'/exe
        if binary.exists() and not os.environ.get('PROP_'+name.upper()+'_MCP_URL'):
            # No gateway/broker/LLM secrets are inherited by market-data MCP processes.
            safe={k:v for k,v in os.environ.items() if k in {'PATH','HOME','LANG','TZ','SSL_CERT_FILE'}}
            children.append(subprocess.Popen([str(binary),*args,'--host','127.0.0.1','--port',str(port)],env=safe,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL))
            os.environ['PROP_'+name.upper()+'_MCP_URL']=f'http://127.0.0.1:{port}/mcp'
    children.append(subprocess.Popen([sys.executable,'-m','prop_connect'],cwd=root))
    while all(c.poll() is None for c in children):time.sleep(.5)
    code=next((c.returncode for c in children if c.poll() is not None),1)
    for c in children:
        if c.poll() is None:c.terminate()
    raise SystemExit(code or 1)
finally:
    for c in children:
        if c.poll() is None:c.terminate()
