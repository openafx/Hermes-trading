"""Install locked libraries into isolated interpreters; never touch Hermes' env."""
import argparse,json,subprocess
from pathlib import Path

p=argparse.ArgumentParser();p.add_argument('--profiles',default='analytics,openbb,vibe,tradingagents,nautilus');p.add_argument('--destination',required=True)
a=p.parse_args();root=Path(__file__).resolve().parents[1];dest=Path(a.destination).absolute();mapping={}
for profile in a.profiles.split(','):
    if profile not in {'service','analytics','openbb','vibe','tradingagents','nautilus'}:raise SystemExit('Unknown profile')
    env=dest/profile
    if env.exists():raise SystemExit('Environment exists; use a fresh destination')
    subprocess.run(['uv','venv','--python','3.12',str(env)],check=True)
    python=str(env/'bin/python')
    subprocess.run(['uv','pip','install','--python',python,'-r',str(root/f'requirements-{profile}.txt')],check=True)
    for name in (['quantstats','vectorbt','pybroker'] if profile=='analytics' else [profile]):mapping['PROP_'+name.upper()+'_PYTHON']=python
dest.mkdir(parents=True,exist_ok=True);(dest/'interpreters.json').write_text(json.dumps(mapping,indent=2));print('Interpreters recorded; no secrets written.')
