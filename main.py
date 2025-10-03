import asyncio
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx
import questionary
import tomli
import mmh3  # 新增依赖
from dotenv import load_dotenv
from rich.console import Console
from rich.logging import RichHandler
from rich.progress import (
    Progress, SpinnerColumn, BarColumn, TextColumn, TimeElapsedColumn,
    DownloadColumn, TransferSpeedColumn
)

# --- 配置日志记录 ---
console = Console()
logging.basicConfig(
    level="INFO",
    format="%(message)s",
    datefmt="[%X]",
    handlers=[RichHandler(console=console, rich_tracebacks=True, show_path=False)],
)
log = logging.getLogger("rich")
logging.getLogger("httpx").setLevel(logging.WARNING)

# --- 全局变量和配置 ---
load_dotenv()
CURSEFORGE_API_KEY = os.getenv("CURSEFORGE_API_KEY", "$2a$10$bL4bIL5pUWqfcO7KQtnMReakwtfHbNKh6v1uTpKlzhwoueEJQnPnm")
use_mirror = True
is_development = os.getenv("DEVELOPMENT") is not None

# API URLs
CF_MIRROR_URL = "https://mod.mcimirror.top"
MR_MIRROR_URL = "https://mod.mcimirror.top"
BMCLAPI_URL = "https://bmclapi2.bangbang93.com"

unzip_path = Path.cwd() / "instance"
DEEARTH_CACHE_PATH = unzip_path / ".deearth_cache.json"

# 已知是通用模组，防止被误识别
KNOWN_UNIVERSAL_MODS = {"geckolib", "supplementaries"}

# --- 动态 URL 函数 ---
def get_cf_api_url():
    return f"{CF_MIRROR_URL}/curseforge" if use_mirror else "https://api.curseforge.com"

def get_mr_api_url():
    return f"{MR_MIRROR_URL}/modrinth" if use_mirror else "https://api.modrinth.com"


# --- 异步 HTTP 客户端 ---
async_client = httpx.AsyncClient(
    headers={"User-Agent": "DeEarthX", "x-api-key": CURSEFORGE_API_KEY},
    follow_redirects=True,
    timeout=60.0,
)

# --- 核心下载逻辑 ---
async def fast_download(download_data: List[Tuple[str, Path, Optional[int]]], desc: str):
    """优化的下载函数，使用 Rich Progress，最多显示 5 个并发下载"""

    async def download_worker(url: str, dest: Path, total_size: Optional[int], progress: Progress, task_id, is_fallback=False):
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            async with async_client.stream("GET", url) as response:
                response.raise_for_status()
                total = int(response.headers.get('content-length', 0)) if total_size is None or total_size == 0 else total_size
                progress.update(task_id, total=total)
                progress.start_task(task_id)

                with dest.open("wb") as f:
                    async for chunk in response.aiter_bytes(chunk_size=65536):
                        f.write(chunk)
                        progress.update(task_id, advance=len(chunk))
        except httpx.HTTPStatusError as e:
            if use_mirror and not is_fallback and (url.startswith(CF_MIRROR_URL) or url.startswith(MR_MIRROR_URL)):
                official_url = ""
                if url.startswith(MR_MIRROR_URL):
                    official_url = url.replace(MR_MIRROR_URL, "https://cdn.modrinth.com")
                elif url.startswith(CF_MIRROR_URL + "/curseforge"):
                    official_url = url.replace(CF_MIRROR_URL, "https://edge.forgecdn.net")

                if official_url:
                    try:
                        progress.reset(task_id)
                        await download_worker(official_url, dest, total_size, progress, task_id, is_fallback=True)
                        return
                    except Exception:
                        pass
            log.warning(f"下载失败: {dest.name} (状态码: {e.response.status_code})")
            progress.update(task_id, description=f"[red]失败: {dest.name}")
        except Exception as e:
            log.warning(f"下载失败: {dest.name} ({str(e)[:50]})")
            progress.update(task_id, description=f"[red]失败: {dest.name}")

    if not download_data:
        return

    semaphore = asyncio.Semaphore(16)
    worker_count = min(5, len(download_data))

    with Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(bar_width=None, complete_style="bright_magenta"),
        "[progress.percentage]{task.percentage:>3.1f}%",
        "•",
        DownloadColumn(),
        "•",
        TransferSpeedColumn(),
        "•",
        TimeElapsedColumn(),
        console=console,
        transient=True
    ) as progress:
        main_task = progress.add_task(f"[cyan]{desc}", total=len(download_data))

        worker_queue = asyncio.Queue()
        for i in range(worker_count):
            worker_queue.put_nowait(progress.add_task(f"worker_{i}", visible=False))

        async def safe_download(url: str, dest: Path, size: Optional[int]):
            task_id = None
            try:
                async with semaphore:
                    task_id = await worker_queue.get()
                    progress.update(task_id, description=f"{dest.name}", total=size or 0, completed=0, visible=True)

                    if not dest.exists():
                        await download_worker(url, dest, size, progress, task_id)
                    else:
                        progress.update(task_id, completed=size or 0)

                    progress.update(main_task, advance=1)
            except Exception as e:
                log.error(f"下载任务 '{dest.name}' 出错: {e}")
            finally:
                if task_id is not None:
                    await asyncio.sleep(0.2)
                    progress.update(task_id, visible=False)
                    worker_queue.put_nowait(task_id)

        tasks = [safe_download(url, path, size) for url, path, size in download_data]
        await asyncio.gather(*tasks, return_exceptions=True)


async def x_fast_download(url: str, dest: Path):
    if not dest.exists():
        try:
            response = await async_client.get(url)
            response.raise_for_status()
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(response.content)
        except Exception as e:
            log.error(f"下载失败 {dest.name}: {e}")

# --- DeEarth 核心逻辑 ---
def load_deearth_cache():
    if DEEARTH_CACHE_PATH.exists():
        try:
            return json.loads(DEEARTH_CACHE_PATH.read_text())
        except json.JSONDecodeError:
            return {}
    return {}

def save_deearth_cache(cache: Dict):
    DEEARTH_CACHE_PATH.write_text(json.dumps(cache, indent=2))

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
    if zip_info and zip_info.get('modinfo', {}).get('type'):
        mod_id = ''
        modinfo_data = zip_info['modinfo']['data']
        if zip_info['modinfo']['type'] == 'forge' and modinfo_data.get('mods'):
            mod_id = modinfo_data['mods'][0].get('modId', '')
        elif zip_info['modinfo']['type'] == 'fabric':
            mod_id = modinfo_data.get('id', '')

        if mod_id in KNOWN_UNIVERSAL_MODS:
            cache[mod_name] = "UNIVERSAL"
            return None

    # 阶段 1: CurseForge API
    try:
        murmur2_hash = _calculate_murmur2_hash(mod_path.read_bytes())
        response = await async_client.post(
            f"{get_cf_api_url()}/v1/fingerprints",
            json={"fingerprints": [murmur2_hash]}
        )
        if response.status_code == 200 and response.json()['data']['exactMatches']:
            mod_id = response.json()['data']['exactMatches'][0]['id']
            # 公开 API 缺乏明确的 side 信息，所以我们继续检查其他源
            log.debug(f"Fingerprint match for {mod_name} on CurseForge (Mod ID: {mod_id}). API lacks side info, proceeding.")
    except Exception as e:
        log.debug(f"CurseForge fingerprint check for {mod_name} failed: {e}")

    # 阶段 2: Modrinth API
    try:
        sha1 = hashlib.sha1(mod_path.read_bytes()).hexdigest()
        response = await async_client.get(f"{get_mr_api_url()}/v2/version_file/{sha1}?algorithm=sha1")
        if response.status_code == 200:
            version_info = response.json()
            project_id = version_info.get('project_id')
            response = await async_client.get(f"{get_mr_api_url()}/v2/project/{project_id}")
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

    # 阶段 3: 元数据检测
    if not zip_info:
        cache[mod_name] = "UNKNOWN"
        return None

    if zip_info.get('modinfo', {}).get('type'):
        modinfo = zip_info['modinfo']
        try:
            if modinfo['type'] == 'forge' and modinfo['data'].get('mods'):
                mod_id = modinfo['data']['mods'][0].get('modId', '')
                deps = modinfo['data'].get('dependencies', {}).get(mod_id, [])
                for dep in deps:
                    if dep.get('modId') in ['minecraft', 'forge', 'neoforge'] and dep.get('side') == 'CLIENT':
                        is_client_side = True
                        break
            elif modinfo['type'] == 'fabric':
                if modinfo['data'].get('environment') == 'client':
                    is_client_side = True

            if is_client_side:
                cache[mod_name] = "CLIENT"
                shutil.move(str(mod_path), str(rubbish_path / mod_name))
                return mod_name
        except Exception:
            pass

    cache[mod_name] = "UNIVERSAL"
    return None

# --- 服务端安装程序 ---
def create_launch_scripts(path: Path, server_type: str, mc_version: str, loader_version: str):
    log.info("创建启动脚本...")

    if server_type in ["forge", "neoforge"]:
        log.info(f"{server_type.capitalize()} 安装程序已创建官方启动脚本 (run.bat/run.sh)。")
        log.info("正在创建 start.bat 和 start.sh 以方便启动...")

        (path / "start.bat").write_text(
            "@echo off\n"
            "call run.bat\n"
            "pause"
        )

        start_sh = path / "start.sh"
        start_sh.write_text(
            "#!/bin/bash\n"
            "./run.sh"
        )
        start_sh.chmod(start_sh.stat().st_mode | 0o111)

    elif server_type in ["fabric", "fabric-loader"]:
        java_command = 'java -Xms4G -Xmx4G'
        args = "-jar fabric-server-launch.jar nogui"

        command_win = f"{java_command} {args}"
        (path / "start.bat").write_text(f"@echo off\n{command_win}\npause")

        command_sh = f"#!/bin/bash\n{command_win}\n"
        start_sh = path / "start.sh"
        start_sh.write_text(command_sh)
        start_sh.chmod(start_sh.stat().st_mode | 0o111)


async def install_server(server_type: str, mc_version: str, loader_version: str, path: Path):
    java_path = "java"

    try:
        subprocess.run([java_path, "-version"], check=True, capture_output=True, text=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        log.error("未找到 Java，请确保已安装 Java 并添加到 PATH")
        return

    log.info(f"正在安装 {server_type} 服务端...")

    try:
        if server_type in ["fabric", "fabric-loader"]:
            installer_url = "https://maven.fabricmc.net/net/fabricmc/fabric-installer/1.0.3/fabric-installer-1.0.3.jar"
            installer_path = path / "fabric-installer.jar"
            await x_fast_download(installer_url, installer_path)

            command = [java_path, "-jar", str(installer_path), "server", "-mcver", mc_version, "-loader", loader_version, "-dir", str(path), "-downloadMinecraft"]
            subprocess.run(command, check=True, cwd=path, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        elif server_type in ["forge", "neoforge"]:
            if server_type == "forge":
                installer_url = f"{BMCLAPI_URL}/forge/download?mcversion={mc_version}&version={loader_version}&category=installer&format=jar"
                installer_path = path / "forge-installer.jar"
            else:
                installer_url = f"{BMCLAPI_URL}/neoforge/version/{loader_version}/download/installer.jar"
                installer_path = path / "neoforge-installer.jar"

            log.info(f"下载 {server_type} 安装器...")
            await x_fast_download(installer_url, installer_path)

            log.info(f"解析 {server_type} 依赖库...")
            library_tasks = []
            with zipfile.ZipFile(installer_path, 'r') as zf:
                mc_info_url = f"{BMCLAPI_URL}/version/{mc_version}/json"
                mc_info = (await async_client.get(mc_info_url)).json()
                for lib in mc_info.get('libraries', []):
                    artifact = lib.get('downloads', {}).get('artifact')
                    if artifact:
                        lib_url = f"https://bmclapi2.bangbang93.com/maven{httpx.URL(artifact['url']).path}"
                        lib_dest = path / "libraries" / artifact['path']
                        library_tasks.append((lib_url, lib_dest, artifact.get('size')))

                for name in ["version.json", "install_profile.json"]:
                    if name in zf.namelist():
                        profile = json.loads(zf.read(name))
                        for lib in profile.get('libraries', []):
                            artifact = lib.get('downloads', {}).get('artifact')
                            if artifact:
                                lib_url = f"https://bmclapi2.bangbang93.com/maven{httpx.URL(artifact['url']).path.replace('/releases', '')}"
                                lib_dest = path / "libraries" / artifact['path']
                                library_tasks.append((lib_url, lib_dest, artifact.get('size')))

            await fast_download(library_tasks, f"下载 {server_type} 依赖库")

            log.info("下载原版服务端 JAR...")
            server_jar_url = f"{BMCLAPI_URL}/version/{mc_version}/server"
            server_jar_dest = path / "libraries" / "net" / "minecraft" / "server" / mc_version / f"server-{mc_version}.jar"
            await x_fast_download(server_jar_url, server_jar_dest)

            log.info(f"运行 {server_type} 安装程序...")
            command = [java_path, "-jar", str(installer_path), "--installServer"]
            subprocess.run(command, check=True, cwd=path, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        log.info(f"{server_type.capitalize()} 服务端安装完成")

    except subprocess.CalledProcessError as e:
        log.error(f"服务端安装失败: {e}")
        output = (e.stderr or e.stdout or b"").decode('utf-8', errors='ignore')
        if output:
            log.error(f"命令输出:\n{output}")
    except Exception as e:
        log.error(f"安装服务端时发生错误: {e}")


# --- 平台处理器 ---
class BasePlatform:
    async def get_info(self, pack_info: Dict[str, Any]) -> Dict[str, str]:
        raise NotImplementedError

    async def download_files(self, pack_info: Dict[str, Any], path: Path):
        raise NotImplementedError


class CurseForge(BasePlatform):
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
            headers={"x-api-key": CURSEFORGE_API_KEY}
        )
        response.raise_for_status()

        files_data = response.json().get('data', [])
        download_tasks = []

        for file_info in files_data:
            if not file_info['fileName'].endswith(".zip"):
                url = file_info.get('downloadUrl')
                if not url:
                    url = f"https://edge.forgecdn.net/files/{file_info['id'] // 1000}/{file_info['id'] % 1000}/{file_info['fileName']}"

                if use_mirror:
                    url = "https://mod.mcimirror.top" + httpx.URL(url).path

                dest = path / "mods" / file_info['fileName']
                download_tasks.append((url, dest, file_info.get('fileLength')))

        await fast_download(download_tasks, "下载 CurseForge 模组")


class Modrinth(BasePlatform):
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
                if use_mirror:
                    url = "https://mod.mcimirror.top" + httpx.URL(url).path
                dest = path / Path(file_info['path'])
                download_tasks.append((url, dest, file_info.get('fileSize')))

        await fast_download(download_tasks, "下载 Modrinth 模组")


def get_platform(dud_files: List[str]) -> Optional[BasePlatform]:
    if "manifest.json" in dud_files:
        return CurseForge()
    if "modrinth.index.json" in dud_files:
        return Modrinth()
    return None


# --- 清理和主逻辑 ---
def cleanup(path: Path):
    """删除不需要的文件和目录"""
    log.info("清理临时文件...")
    items_to_remove = [
        "installer.log", "installer.jar", "fabric-installer.jar",
        "forge-installer.jar", "neoforge-installer.jar",
        "options.txt",
    ]
    dirs_to_remove = ["shaderpacks", "resourcepacks", "essential"]

    for item in items_to_remove:
        item_path = path / item
        try:
            if item_path.is_file() or item_path.is_symlink():
                item_path.unlink()
        except Exception:
            pass

    for item in dirs_to_remove:
        item_path = path / item
        try:
            if item_path.is_dir():
                shutil.rmtree(item_path, ignore_errors=True)
        except Exception:
            pass


async def main_logic(modpack_path_str: str):
    modpack_path = Path(modpack_path_str)
    if not modpack_path.exists():
        log.error(f"整合包路径不存在: {modpack_path}")
        return

    zip_name = modpack_path.stem
    instance_dir = unzip_path / zip_name
    instance_dir.mkdir(parents=True, exist_ok=True)

    log.info(f"正在解压整合包到: {instance_dir}")

    dud_files = []
    pack_info = {}

    try:
        with zipfile.ZipFile(modpack_path, 'r') as zf:
            for member in zf.infolist():
                if member.is_dir():
                    continue
                name = member.filename
                if name.startswith("overrides/"):
                    target_path = instance_dir / name[len("overrides/"):]
                    target_path.parent.mkdir(parents=True, exist_ok=True)
                    target_path.write_bytes(zf.read(name))
                elif name.endswith((".json", "mcbbs.packmeta")):
                    dud_files.append(name)
                    if name in ["modrinth.index.json", "manifest.json"]:
                        pack_info = json.loads(zf.read(name).decode('utf-8'))
    except zipfile.BadZipFile:
        log.error("文件不是有效的 ZIP 压缩包")
        return
    except Exception as e:
        log.error(f"解压整合包时发生错误: {e}")
        return

    log.info("解压完成")

    platform = get_platform(dud_files)
    if not platform:
        log.error("无法识别的整合包平台，仅支持 CurseForge 和 Modrinth")
        return

    info = await platform.get_info(pack_info)
    log.info(f"整合包信息: MC {info['minecraft']}, 加载器 {info['loader']}@{info['loader_version']}")

    await platform.download_files(pack_info, instance_dir)

    await deearth_main(instance_dir / "mods", instance_dir / ".rubbish")

    await install_server(info['loader'], info['minecraft'], info['loader_version'], instance_dir)

    (instance_dir / "eula.txt").write_text(
        "#By changing the setting below to TRUE you are indicating your agreement to our EULA (https://aka.ms/MinecraftEULA).\n"
        "#This serverpack created by DeEarthX\n"
        "eula=true",
        encoding='utf-8'
    )

    create_launch_scripts(instance_dir, info['loader'], info['minecraft'], info['loader_version'])

    cleanup(instance_dir)

    log.info(f"✓ 服务端整合包制作完成！")
    log.info(f"  路径: {instance_dir.resolve()}")


async def cli_main():
    """CLI 入口点"""
    global use_mirror
    init_dir()

    try:
        use_mirror = await questionary.confirm("是否优先使用镜像源 (推荐)?", default=True).ask_async()
    except Exception:
        answer = input("是否优先使用镜像源 (推荐)? (Y/n) ")
        use_mirror = not answer.lower().startswith('n')


    modpack_path = None
    if len(sys.argv) > 1:
        modpack_path = sys.argv[1]
    else:
        try:
            modpack_path = await questionary.path("请输入整合包路径:").ask_async()
        except Exception:
            modpack_path = input("请输入整合包路径: ")

    if modpack_path:
        modpack_path = modpack_path.strip().strip("'\"")

    if modpack_path and Path(modpack_path).exists():
        await main_logic(modpack_path)
    elif modpack_path:
        log.error(f"提供的路径不存在: '{modpack_path}'")
    else:
        log.info("操作已取消")

    await async_client.aclose()


def init_dir():
    """初始化工作目录"""
    if not unzip_path.exists():
        (unzip_path / ".rubbish").mkdir(parents=True, exist_ok=True)


if __name__ == "__main__":
    try:
        asyncio.run(cli_main())
    except KeyboardInterrupt:
        log.info("\n操作被用户中断")
    except Exception as e:
        log.error(f"发生未处理的错误: {e}")
        console.print_exception(show_locals=False)
