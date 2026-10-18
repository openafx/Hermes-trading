import os
import uvicorn
from .app import create_app
if __name__=='__main__':
    uvicorn.run(create_app(),host=os.environ.get('PROP_BIND','127.0.0.1'),port=int(os.environ.get('PORT','8765')),access_log=False)
