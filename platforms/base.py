import abc
from pathlib import Path
from typing import Any, Dict

from utils.exceptions import PlatformError

class BasePlatform(abc.ABC):
    @abc.abstractmethod
    async def get_info(self, pack_info: Dict[str, Any]) -> Dict[str, str]:
        """从整合包信息中提取 Minecraft 版本和加载器信息。"""
        ...

    @abc.abstractmethod
    async def download_files(self, pack_info: Dict[str, Any], path: Path):
        """根据整合包信息下载所有模组文件。"""
        ...
        
    def validate_pack_info(self, pack_info: Dict[str, Any]):
        """
        验证整合包信息是否有效。
        这个基类方法提供了通用检查。
        如果无效则抛出 PlatformError。
        """
        if not pack_info or not isinstance(pack_info, dict):
            raise PlatformError("整合包信息 (pack_info) 无效或为空。")