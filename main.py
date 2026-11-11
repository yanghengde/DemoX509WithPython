"""Web 入口：python main.py。"""
import sys

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, 'reconfigure'):
        stream.reconfigure(encoding='utf-8')

import uvicorn
from config import APP_HOST, APP_PORT
from web.app import create_app

app = create_app()

if __name__ == '__main__':
    uvicorn.run(app, host=APP_HOST, port=APP_PORT, reload=False)
