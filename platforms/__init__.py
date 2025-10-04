from typing import List, Optional

from .base import BasePlatform
from .curseforge import CurseForge
from .modrinth import Modrinth

def get_platform(dud_files: List[str]) -> Optional[BasePlatform]:
    if "manifest.json" in dud_files:
        return CurseForge()
    if "modrinth.index.json" in dud_files:
        return Modrinth()
    return None