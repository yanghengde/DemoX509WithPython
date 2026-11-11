"""独立只读测试：.venv\Scripts\python.exe connect_opcenter.py --top 5。"""
import argparse
import asyncio
import json
import sys

from connectors.opcenter.client import OpcenterX509Client
from connectors.opcenter.materials import MaterialService
from fastapi import HTTPException
from web.opcenter import call


async def run(args):
    service = MaterialService(OpcenterX509Client.from_config())
    try:
        check = await call(service.check_connection)
        data = await call(lambda: service.list_materials(top=args.top, keyword=args.keyword))
        print(json.dumps({'connection': check, 'materials': data}, ensure_ascii=False, indent=2))
        return 0
    except HTTPException as exc:
        print(json.dumps({'ok': False, **exc.detail}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1
    finally:
        await service.close()


if __name__ == '__main__':
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description='X509 连接测试并读取 Opcenter 物料')
    parser.add_argument('--top', type=int, choices=range(1, 101), default=5, metavar='1-100')
    parser.add_argument('--keyword', default='')
    sys.exit(asyncio.run(run(parser.parse_args())))
