import asyncio
import hashlib
import json
import shutil
import zipfile
from pathlib import Path
from typing import Any, Dict, Optional

import mmh3
import tomli
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn

from utils.logger import log, console
from downloader import async_client
from config import config  # 修正导入
from constants import KNOWN_UNIVERSAL_MODS, get_mr_api_url, get_cf_api_url, DEEARTH_API_URL

def load_deearth_cache():
    if config.DEEARTH_CACHE_PATH.exists(): # 修正访问方式
        try:
            return json.loads(config.DEEARTH_CACHE_PATH.read_text())
        except json.JSONDecodeError:
            return {}
    return {}

def save_deearth_cache(cache: Dict):
    config.DEEARTH_CACHE_PATH.write_text(json.dumps(cache, indent=2)) # 修正访问方式

def get_zip_info(mod_path: Path) -> Optional[Dict[str, Any]]:
    try:
        with zipfile.ZipFile(mod_path, 'r') as zf:
            info = {'modinfo': {'type': '', 'data': {}}}
            for name in zf.namelist():
                if name.endswith("mods.toml") or name.endswith("neoforge.mods.toml"):
                    info['modinfo']['type'] = "forge"
                    info['modinfo']['data'] = tomli.loads(zf.read(name).decode('utf-8'))
                    return info
                if name.endswith("fabric.mod.json"):
                    info['modinfo']['type'] = "fabric"
                    info['modinfo']['data'] = json.loads(zf.read(name).decode('utf-8'))
                    return info
            return info
    except Exception:
        return None

def _calculate_murmur2_hash(data: bytes) -> int:
    normalized_data = bytes(filter(lambda b: b not in b'\r\n\t ', data))
    return mmh3.hash(normalized_data, seed=1, signed=True)

async def _check_modrinth(sha1: str) -> Optional[bool]:
    try:
        response = await async_client.get(f"{get_mr_api_url()}/v2/version_file/{sha1}?algorithm=sha1")
        if response.status_code == 200:
            version_info = response.json()
            project_id = version_info.get('project_id')
            response = await async_client.get(f"{get_mr_api_url()}/v2/project/{project_id}")
            project_info = response.json()
            client = project_info.get('client_side')
            server = project_info.get('server_side')
            return client == 'required' and server != 'required'
    except Exception:
        pass
    return None

async def _check_curseforge(murmur2_hash: int):
    try:
        response = await async_client.post(
            f"{get_cf_api_url()}/v1/fingerprints",
            json={"fingerprints": [murmur2_hash]}
        )
        if response.status_code == 200 and response.json()['data']['exactMatches']:
            return response.json()['data']['exactMatches'][0]
    except Exception:
        pass
    return None

async def _check_deearth_api(mod_id: str) -> Optional[bool]:
    if not mod_id:
        return None
    try:
        response = await async_client.get(f"{DEEARTH_API_URL}/modid?modid={mod_id}")
        if response.status_code == 200:
            data = response.json()
            return data.get('client') == 'required' and data.get('server') != 'required'
    except Exception:
        pass
    return None

async def deearth(mod_path: Path, rubbish_path: Path, cache: Dict) -> Optional[str]:
    mod_name = mod_path.name
    if mod_name in cache:
        if cache[mod_name] == "CLIENT":
            shutil.move(str(mod_path), str(rubbish_path / mod_name))
            return mod_name
        return None

    try:
        mod_bytes = mod_path.read_bytes()
        zip_info = get_zip_info(mod_path)
        
        mod_id = ''
        modinfo_data = {}
        if zip_info and zip_info.get('modinfo', {}).get('type'):
            modinfo_data = zip_info['modinfo']['data']
            if zip_info['modinfo']['type'] == 'forge' and modinfo_data.get('mods'):
                mod_id = modinfo_data['mods'][0].get('modId', '')
            elif zip_info['modinfo']['type'] == 'fabric':
                mod_id = modinfo_data.get('id', '')
        
        if mod_id in KNOWN_UNIVERSAL_MODS:
            cache[mod_name] = "UNIVERSAL"
            return None

        sha1 = hashlib.sha1(mod_bytes).hexdigest()
        murmur2_hash = _calculate_murmur2_hash(mod_bytes)
        
        results = await asyncio.gather(
            _check_modrinth(sha1),
            _check_deearth_api(mod_id),
            _check_curseforge(murmur2_hash)
        )
        
        is_client_side = None
        
        if results[0] is not None:
            is_client_side = results[0]
            log.debug(f"{mod_name}: Modrinth API 判定结果: {'客户端' if is_client_side else '通用'}")
        elif results[1] is not None:
            is_client_side = results[1]
            log.debug(f"{mod_name}: DeEarth API 判定结果: {'客户端' if is_client_side else '通用'}")

        if is_client_side is None and zip_info and zip_info.get('modinfo', {}).get('type'):
            modinfo = zip_info['modinfo']
            log.debug(f"{mod_name}: API 未命中，回退到本地元数据分析")
            if modinfo['type'] == 'forge' and modinfo_data.get('mods'):
                deps = modinfo_data.get('dependencies', {}).get(mod_id, [])
                for dep in deps:
                    if dep.get('modId') in ['minecraft', 'forge', 'neoforge'] and dep.get('side') == 'CLIENT':
                        is_client_side = True
                        break
            elif modinfo['type'] == 'fabric':
                if modinfo_data.get('environment') == 'client':
                    is_client_side = True
        
        final_status = "UNKNOWN"
        if is_client_side is True:
            final_status = "CLIENT"
            shutil.move(str(mod_path), str(rubbish_path / mod_name))
            return mod_name
        elif is_client_side is False:
            final_status = "UNIVERSAL"
        
        cache[mod_name] = final_status

    except Exception as e:
        log.error(f"处理模组 {mod_name} 时出错: {e}")
        cache[mod_name] = "ERROR"

    return None


async def deearth_main(mods_path: Path, rubbish_path: Path):
    log.info("开始筛选客户端模组...")
    mods_path.mkdir(exist_ok=True)
    rubbish_path.mkdir(exist_ok=True)
    jar_files = list(mods_path.glob("*.jar"))
    cache = load_deearth_cache()
    client_mods = []
    
    with Progress(
        SpinnerColumn(), TextColumn("[progress.description]{task.description}"),
        BarColumn(), TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TextColumn("({task.completed}/{task.total})"), console=console
    ) as progress:
        task = progress.add_task(f"[cyan]筛选 {len(jar_files)} 个模组", total=len(jar_files))
        semaphore = asyncio.Semaphore(config.deearth_concurrency)
        
        async def process_mod(mod_path: Path):
            async with semaphore:
                result = await deearth(mod_path, rubbish_path, cache)
                progress.update(task, advance=1)
                return result
        
        results = await asyncio.gather(*[process_mod(jar) for jar in jar_files])
        client_mods = [r for r in results if r]
        
    save_deearth_cache(cache)
    
    if client_mods:
        log.info(f"已移除 {len(client_mods)} 个客户端模组")
    else:
        log.info("未检测到客户端专用模组")