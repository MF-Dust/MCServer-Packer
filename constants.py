from config import config

# --- 动态 URL 函数 ---
def get_cf_api_url():
    return f"{CF_MIRROR_URL}/curseforge" if config.use_mirror else "https://api.curseforge.com"

def get_mr_api_url():
    return f"{MR_MIRROR_URL}/modrinth" if config.use_mirror else "https://api.modrinth.com"

# API URLs
CF_MIRROR_URL = "https://mod.mcimirror.top"
MR_MIRROR_URL = "https://mod.mcimirror.top"
BMCLAPI_URL = "https://bmclapi2.bangbang93.com"
DEEARTH_API_URL = "https://dearth.0771010.xyz/api"

# 已知是通用模组，防止被误识别
KNOWN_UNIVERSAL_MODS = {"geckolib", "supplementaries"}

# 文件名
MANIFEST_JSON = "manifest.json"
MODRINTH_INDEX_JSON = "modrinth.index.json"