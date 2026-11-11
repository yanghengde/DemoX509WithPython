"""框架配置；默认不连接产品或模型。"""
import os
from pathlib import Path
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent
load_dotenv(PROJECT_ROOT / '.env')


def flag(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).lower() in {'true', '1', 'yes'}


def csv_env(name: str, default: str = '') -> list[str]:
    return [item.strip() for item in os.getenv(name, default).split(',') if item.strip()]


def data_path(name: str, default: str) -> str:
    path = Path(os.getenv(name, default))
    return str(path if path.is_absolute() else PROJECT_ROOT / path)


STATIC_DIR = PROJECT_ROOT / 'static'
DATA_DIR = Path(data_path('DATA_DIR', 'data'))
APP_USERNAME = os.getenv('APP_USERNAME', 'local-user')
APP_HOST = os.getenv('APP_HOST', '127.0.0.1')
APP_PORT = int(os.getenv('APP_PORT', '8031'))
PRODUCT_NAME = os.getenv('PRODUCT_NAME', 'Demo')
PRODUCT_BASE_URL = os.getenv('PRODUCT_BASE_URL', '').strip().rstrip('/')
PRODUCT_AUTH_MODE = os.getenv('PRODUCT_AUTH_MODE', 'none').lower()
PRODUCT_USERNAME = os.getenv('PRODUCT_USERNAME', '')
PRODUCT_PASSWORD = os.getenv('PRODUCT_PASSWORD', '')
PRODUCT_TOKEN = os.getenv('PRODUCT_TOKEN', '')
PRODUCT_API_KEY = os.getenv('PRODUCT_API_KEY', '')
PRODUCT_API_KEY_HEADER = os.getenv('PRODUCT_API_KEY_HEADER', 'X-API-Key')
PRODUCT_TIMEOUT = float(os.getenv('PRODUCT_TIMEOUT', '30'))
PRODUCT_VERIFY_TLS = flag('PRODUCT_VERIFY_TLS', True)
PRODUCT_TRUST_ENV = flag('PRODUCT_TRUST_ENV', False)
OPCENTER_BASE_URL = os.getenv('OPCENTER_BASE_URL', 'http://opcenter2607/sit-svc/Siemens/Application/AppU4DM/odata').strip().rstrip('/')
OPCENTER_TIMEOUT = float(os.getenv('OPCENTER_TIMEOUT', '30'))
OPCENTER_VERIFY_TLS = flag('OPCENTER_VERIFY_TLS', True)
OPCENTER_X509_SOURCE = os.getenv('OPCENTER_X509_SOURCE', 'store').strip().lower()
OPCENTER_X509_THUMBPRINT = os.getenv('OPCENTER_X509_THUMBPRINT', '').strip()
OPCENTER_X509_STORE_LOCATION = os.getenv('OPCENTER_X509_STORE_LOCATION', 'LocalMachine').strip()
OPCENTER_X509_PFX_PATH = os.getenv('OPCENTER_X509_PFX_PATH', '').strip()
if OPCENTER_X509_PFX_PATH and not Path(OPCENTER_X509_PFX_PATH).is_absolute():
    OPCENTER_X509_PFX_PATH = str(PROJECT_ROOT / OPCENTER_X509_PFX_PATH)
OPCENTER_X509_PFX_PASSWORD = os.getenv('OPCENTER_X509_PFX_PASSWORD', '')
OPCENTER_X509_ISSUER = os.getenv('OPCENTER_X509_ISSUER', 'urn:unifiedoauth')
OPCENTER_X509_AUDIENCE = os.getenv('OPCENTER_X509_AUDIENCE', 'urn:unified')
OPCENTER_X509_LIFETIME = int(os.getenv('OPCENTER_X509_LIFETIME', '120'))
LLM_API_KEY = os.getenv('LLM_API_KEY', '')
LLM_BASE_URL = os.getenv('LLM_BASE_URL', '').strip()
LLM_MODEL = os.getenv('LLM_MODEL', '').strip()
LLM_TIMEOUT = float(os.getenv('LLM_TIMEOUT', '45'))
MAX_TOOL_LOOPS = max(1, int(os.getenv('MAX_TOOL_LOOPS', '15')))
MAX_READ_FAILURES = max(1, int(os.getenv('MAX_READ_FAILURES', '3')))
LANGGRAPH_RECURSION_LIMIT = max(25, int(os.getenv('LANGGRAPH_RECURSION_LIMIT', str(MAX_TOOL_LOOPS * 6 + 10))))
AGENT_ENGINE = 'langgraph'
MEMORY_FILE = str(DATA_DIR / 'memory.json')
SESSIONS_DIR = str(DATA_DIR / 'sessions')
LANGGRAPH_CHECKPOINT_DB = data_path('LANGGRAPH_CHECKPOINT_DB', str(DATA_DIR / 'langgraph_checkpoints.sqlite'))
EXPERIENCE_DB = data_path('EXPERIENCE_DB', str(DATA_DIR / 'agent_experience.sqlite'))
CHAT_RUN_DB = data_path('CHAT_RUN_DB', str(DATA_DIR / 'chat_runs.sqlite'))
SAFE_CREATE_THRESHOLD = int(os.getenv('SAFE_CREATE_THRESHOLD', '20'))
SAFE_UPDATE_THRESHOLD = int(os.getenv('SAFE_UPDATE_THRESHOLD', '3'))
SAFE_DELETE_THRESHOLD = int(os.getenv('SAFE_DELETE_THRESHOLD', '0'))
ENABLE_PERFORMANCE_LOG = flag('ENABLE_PERFORMANCE_LOG', True)
ENABLE_MCP_HTTP = flag('ENABLE_MCP_HTTP')
MCP_HTTP_PATH = '/' + os.getenv('MCP_HTTP_PATH', 'mcp').strip('/')
if MCP_HTTP_PATH == '/':
    MCP_HTTP_PATH = '/mcp'
MCP_API_KEY = os.getenv('MCP_API_KEY', '').strip()
MCP_ALLOWED_HOSTS = csv_env('MCP_ALLOWED_HOSTS', 'localhost:*,127.0.0.1:*,[::1]:*')
MCP_ALLOWED_ORIGINS = csv_env('MCP_ALLOWED_ORIGINS')
