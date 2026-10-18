"""Generate a merge proposal for the EXISTING Hermes config, no overwrite."""
import argparse
import pathlib
import json
import yaml
from urllib.parse import urlsplit

def proposal(config_path,output_path,url):
    config_path=pathlib.Path(config_path).expanduser();output_path=pathlib.Path(output_path).expanduser()
    if config_path.resolve()==output_path.resolve() or output_path.exists():raise ValueError('La proposition doit être un nouveau fichier, distinct de la configuration existante')
    u=urlsplit(url)
    if u.scheme not in {'http','https'} or not u.netloc or u.username or u.password:raise ValueError('URL MCP invalide')
    original=yaml.safe_load(config_path.read_text()) or {}
    if not isinstance(original,dict):raise ValueError('Configuration Hermes invalide')
    servers=original.setdefault('mcp_servers',{})
    if 'prop_connect' in servers:raise ValueError('Serveur prop_connect déjà défini ; aucune proposition écrasée')
    servers['prop_connect']={'url':url,'headers':{'Authorization':'Bearer ${PROP_ADMIN_TOKEN}'},'enabled':True,'timeout':180,'trust':'untrusted','tools':{'include':['trading_report','integrations_status','investor_skills_list','investor_skill_read','research_catalog_read','research_submit','research_result'],'resources':False,'prompts':False}}
    output_path.parent.mkdir(parents=True,exist_ok=True)
    # Proposal contains references, not secrets. Original file stays byte-for-byte intact.
    output_path.write_text(yaml.safe_dump(original,sort_keys=False,allow_unicode=True))
    return {'proposal_written':True,'original_modified':False,'server':'prop_connect'}

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--config',required=True);p.add_argument('--output',required=True);p.add_argument('--mcp-url',required=True);a=p.parse_args()
    print(json.dumps(proposal(a.config,a.output,a.mcp_url),indent=2))
