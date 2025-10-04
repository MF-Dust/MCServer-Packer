from pathlib import Path
from typing import Any, Dict

import httpx

from .base import BasePlatform
from downloader import fast_download
from utils.logger import log # <--- 新增导入
from config import config
from constants import get_mr_api_url
from utils.exceptions import PlatformError

class Modrinth(BasePlatform):
    def validate_pack_info(self, pack_info: Dict[str, Any]):
        """验证 Modrinth 整合包信息。"""
        super().validate_pack_info(pack_info)
        if 'dependencies' not in pack_info or not isinstance(pack_info.get('dependencies'), dict):
            raise PlatformError("Modrinth modrinth.index.json 格式无效：缺少 'dependencies' 键。")

    async def get_info(self, pack_info: Dict[str, Any]) -> Dict[str, str]:
        deps = pack_info['dependencies']
        info = {'minecraft': deps.get('minecraft', 'unknown'), 'loader': 'unknown', 'loader_version': 'unknown'}
        loaders = ["forge", "neoforge", "fabric-loader"]
        for loader in loaders:
            if loader in deps:
                info['loader'] = loader
                info['loader_version'] = deps[loader]
                break
        return info

    async def download_files(self, pack_info: Dict[str, Any], path: Path):
        log.info("从 Modrinth 下载模组...")
        download_tasks = []

        for file_info in pack_info['files']:
            if not file_info['path'].endswith(".zip"):
                url = file_info['downloads'][0]
                if config.use_mirror:
                    url = "https://mod.mcimirror.top" + httpx.URL(url).path
                dest = path / Path(file_info['path'])
                download_tasks.append((url, dest, file_info.get('fileSize')))

        await fast_download(download_tasks, "下载 Modrinth 模组")