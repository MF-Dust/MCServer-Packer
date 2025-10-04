from pathlib import Path
from typing import Any, Dict

import httpx

from .base import BasePlatform
from downloader import fast_download, async_client
from utils.logger import log # <--- 新增导入
from config import config
from constants import get_cf_api_url
from utils.exceptions import PlatformError

class CurseForge(BasePlatform):
    def validate_pack_info(self, pack_info: Dict[str, Any]):
        """验证 CurseForge 整合包信息。"""
        super().validate_pack_info(pack_info)
        if 'minecraft' not in pack_info or 'version' not in pack_info.get('minecraft', {}) or 'modLoaders' not in pack_info.get('minecraft', {}):
            raise PlatformError("CurseForge manifest.json 格式无效：缺少 'minecraft', 'version', 或 'modLoaders' 键。")

    async def get_info(self, pack_info: Dict[str, Any]) -> Dict[str, str]:
        info = {'minecraft': pack_info['minecraft']['version'], 'loader': 'unknown', 'loader_version': 'unknown'}
        loader_id = pack_info['minecraft']['modLoaders'][0]['id']
        parts = loader_id.split('-', 1)
        if len(parts) == 2:
            info['loader'], info['loader_version'] = parts
        return info

    async def download_files(self, pack_info: Dict[str, Any], path: Path):
        log.info("从 CurseForge 下载模组...")
        file_ids = [file['fileID'] for file in pack_info['files']]

        response = await async_client.post(
            f"{get_cf_api_url()}/v1/mods/files",
            json={"fileIds": file_ids},
            headers={"x-api-key": config.CURSEFORGE_API_KEY}
        )
        response.raise_for_status()

        files_data = response.json().get('data', [])
        download_tasks = []

        for file_info in files_data:
            if not file_info['fileName'].endswith(".zip"):
                url = file_info.get('downloadUrl')
                if not url:
                    url = f"https://edge.forgecdn.net/files/{file_info['id'] // 1000}/{file_info['id'] % 1000}/{file_info['fileName']}"

                if config.use_mirror:
                    url = "https://mod.mcimirror.top" + httpx.URL(url).path

                dest = path / "mods" / file_info['fileName']
                download_tasks.append((url, dest, file_info.get('fileLength')))

        await fast_download(download_tasks, "下载 CurseForge 模组")