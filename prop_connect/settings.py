from dataclasses import dataclass
from pathlib import Path
import os
from urllib.parse import urlsplit


@dataclass
class Settings:
    root: Path
    data: Path
    admin_token: str
    public_url: str = ''

    @classmethod
    def load(cls):
        root=Path(__file__).resolve().parents[1]
        token=os.environ.get('PROP_ADMIN_TOKEN','')
        if len(token)<32:raise RuntimeError('PROP_ADMIN_TOKEN doit contenir au moins 32 caractères ; ne pas le publier dans le chat.')
        data=Path(os.environ.get('PROP_DATA_DIR',str(root/'data'))).resolve()
        if os.environ.get('RAILWAY_ENVIRONMENT_ID') and not os.environ.get('PROP_DATA_DIR'):
            raise RuntimeError('Railway : définir PROP_DATA_DIR sur un volume persistant dédié.')
        url=os.environ.get('PROP_PUBLIC_BASE_URL','').rstrip('/')
        if url and (urlsplit(url).scheme not in {'http','https'} or urlsplit(url).path not in {'','/'}):
            raise RuntimeError('PROP_PUBLIC_BASE_URL : origine HTTP(S) uniquement')
        if os.environ.get('RAILWAY_ENVIRONMENT_ID') and not url.startswith('https://'):
            raise RuntimeError('Railway : PROP_PUBLIC_BASE_URL HTTPS requis')
        data.mkdir(parents=True,exist_ok=True)
        return cls(root,data,token,url)
