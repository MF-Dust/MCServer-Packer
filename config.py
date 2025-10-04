import os
from pathlib import Path
from dotenv import load_dotenv

# --- 全局变量和配置 ---
load_dotenv()
CURSEFORGE_API_KEY = os.getenv("CURSEFORGE_API_KEY", "$2a$10$bL4bIL5pUWqfcO7KQtnMReakwtfHbNKh6v1uTpKlzhwoueEJQnPnm")

# 这个变量会在 main.py 中被用户输入所修改
use_mirror = True

is_development = os.getenv("DEVELOPMENT") is not None

# API URLs
CF_MIRROR_URL = "https://mod.mcimirror.top"
MR_MIRROR_URL = "https://mod.mcimirror.top"
BMCLAPI_URL = "https://bmclapi2.bangbang93.com"
DEEARTH_API_URL = "https://dearth.0771010.xyz/api"  # 新增

# 工作路径
unzip_path = Path.cwd() / "instance"
DEEARTH_CACHE_PATH = unzip_path / ".deearth_cache.json"

# 已知是通用模组，防止被误识别
KNOWN_UNIVERSAL_MODS = {"geckolib", "supplementaries"}

# --- 动态 URL 函数 ---
def get_cf_api_url():
    return f"{CF_MIRROR_URL}/curseforge" if use_mirror else "https://api.curseforge.com"

def get_mr_api_url():
    return f"{MR_MIRROR_URL}/modrinth" if use_mirror else "https://api.modrinth.com"