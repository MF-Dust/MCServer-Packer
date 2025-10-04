import asyncio
import hashlib
import json
import logging
import shutil
import zipfile
from pathlib import Path
from typing import Any, Dict, Optional

import mmh3
import tomli
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn

from utils.logger import log, console
from downloader import async_client
import config

def load_deearth_cache():
    if config.DEEARTH_CACHE_PATH.exists():
        try:
            return json.loads(config.DEEARTH_CACHE_PATH.read_text())
        except json.JSONDecodeError:
            return {}
    return {}

def save_deearth_cache(cache: Dict):
    config.DEEARTH_CACHE_PATH.write_text(json.dumps(cache, indent=2))

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

async def deearth(mod_path: Path, rubbish_path: Path, cache: Dict) -> Optional[str]:
    mod_name = mod_path.name

    if mod_name in cache:
        if cache[mod_name] == "CLIENT":
            shutil.move(str(mod_path), str(rubbish_path / mod_name))
            return mod_name
        return None

    is_client_side = False
    
    zip_info = get_zip_info(mod_path)
    mod_id = ''
    if zip_info and zip_info.get('modinfo', {}).get('type'):
        modinfo_data = zip_info['modinfo']['data']
        if zip_info['modinfo']['type'] == 'forge' and modinfo_data.get('mods'):
            mod_id = modinfo_data['mods'][0].get('modId', '')
        elif zip_info['modinfo']['type'] == 'fabric':
            mod_id = modinfo_data.get('id', '')
    
    if mod_id in config.KNOWN_UNIVERSAL_MODS:
        cache[mod_name] = "UNIVERSAL"
        return None

    # 阶段 1: CurseForge API
    try:
        murmur2_hash = _calculate_murmur2_hash(mod_path.read_bytes())
        response = await async_client.post(
            f"{config.get_cf_api_url()}/v1/fingerprints",
            json={"fingerprints": [murmur2_hash]}
        )
        if response.status_code == 200 and response.json()['data']['exactMatches']:
            cf_mod_id = response.json()['data']['exactMatches'][0]['id']
            log.debug(f"Fingerprint match for {mod_name} on CurseForge (Mod ID: {cf_mod_id}). API lacks side info, proceeding.")
    except Exception as e:
        log.debug(f"CurseForge fingerprint check for {mod_name} failed: {e}")

    # 阶段 2: Modrinth API
    try:
        sha1 = hashlib.sha1(mod_path.read_bytes()).hexdigest()
        response = await async_client.get(f"{config.get_mr_api_url()}/v2/version_file/{sha1}?algorithm=sha1")
        if response.status_code == 200:
            version_info = response.json()
            project_id = version_info.get('project_id')
            response = await async_client.get(f"{config.get_mr_api_url()}/v2/project/{project_id}")
            project_info = response.json()
            client = project_info.get('client_side')
            server = project_info.get('server_side')
            if client == 'required' and server != 'required':
                is_client_side = True
            cache[mod_name] = "CLIENT" if is_client_side else "UNIVERSAL"
            if is_client_side:
                shutil.move(str(mod_path), str(rubbish_path / mod_name))
                return mod_name
            return None
    except Exception:
        pass

    # 阶段 3: DeEarth API
    if mod_id:
        try:
            response = await async_client.get(f"{config.DEEARTH_API_URL}/modid?modid={mod_id}")
            if response.status_code == 200:
                data = response.json()
                client_side = data.get('client')
                server_side = data.get('server')

                if client_side == 'required' and server_side != 'required':
                    log.debug(f"DeEarth API identified {mod_name} as client-side.")
                    is_client_side = True
                
                cache[mod_name] = "CLIENT" if is_client_side else "UNIVERSAL"
                if is_client_side:
                    shutil.move(str(mod_path), str(rubbish_path / mod_name))
                    return mod_name
                return None
        except Exception as e:
            log.debug(f"DeEarth API check for {mod_id} failed: {e}")

    # 阶段 4: 元数据检测
    if not zip_info:
        cache[mod_name] = "UNKNOWN"
        return None

    if zip_info.get('modinfo', {}).get('type'):
        modinfo = zip_info['modinfo']
        try:
            if modinfo['type'] == 'forge' and modinfo_data.get('mods'):
                mod_id = modinfo_data['mods'][0].get('modId', '')
                deps = modinfo_data.get('dependencies', {}).get(mod_id, [])
                for dep in deps:
                    if dep.get('modId') in ['minecraft', 'forge', 'neoforge'] and dep.get('side') == 'CLIENT':
                        is_client_side = True
                        break
            elif modinfo['type'] == 'fabric':
                if modinfo_data.get('environment') == 'client':
                    is_client_side = True

            if is_client_side:
                cache[mod_name] = "CLIENT"
                shutil.move(str(mod_path), str(rubbish_path / mod_name))
                return mod_name
        except Exception:
            pass

    cache[mod_name] = "UNIVERSAL"
    return None

async def deearth_main(mods_path: Path, rubbish_path: Path):
    log.info("开始筛选客户端模组...")
    mods_path.mkdir(exist_ok=True)
    rubbish_path.mkdir(exist_ok=True)

    jar_files = list(mods_path.glob("*.jar"))
    cache = load_deearth_cache()

    client_mods = []

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TextColumn("({task.completed}/{task.total})"),
        console=console,
    ) as progress:
        task = progress.add_task(f"[cyan]筛选 {len(jar_files)} 个模组", total=len(jar_files))

        semaphore = asyncio.Semaphore(10)

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