"""运行数据隔离到临时目录，不污染开发者会话。"""
import atexit
import os
import shutil
import tempfile

_data = tempfile.mkdtemp(prefix='demo-framework-tests-')
os.environ['DATA_DIR'] = _data
os.environ['LLM_BASE_URL'] = ''
os.environ['LLM_MODEL'] = ''
os.environ['ENABLE_MCP_HTTP'] = 'False'
os.environ['PRODUCT_AUTH_MODE'] = 'none'
os.environ['PRODUCT_BASE_URL'] = ''
atexit.register(shutil.rmtree, _data, ignore_errors=True)
