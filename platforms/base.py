import abc
from pathlib import Path
from typing import Any, Dict

class BasePlatform(abc.ABC):
    @abc.abstractmethod
    async def get_info(self, pack_info: Dict[str, Any]) -> Dict[str, str]:
        ...

    @abc.abstractmethod
    async def download_files(self, pack_info: Dict[str, Any], path: Path):
        ...