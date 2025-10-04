import json
import re
import subprocess
import sys
import zipfile
from pathlib import Path

import httpx

from utils.logger import log
from downloader import fast_download, x_fast_download, async_client
import config

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
                installer_url = f"{config.BMCLAPI_URL}/forge/download?mcversion={mc_version}&version={loader_version}&category=installer&format=jar"
                installer_path = path / "forge-installer.jar"
            else:
                installer_url = f"{config.BMCLAPI_URL}/neoforge/version/{loader_version}/download/installer.jar"
                installer_path = path / "neoforge-installer.jar"

            log.info(f"下载 {server_type} 安装器...")
            await x_fast_download(installer_url, installer_path)

            log.info(f"解析 {server_type} 依赖库...")
            library_tasks = []
            with zipfile.ZipFile(installer_path, 'r') as zf:
                mc_info_url = f"{config.BMCLAPI_URL}/version/{mc_version}/json"
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
            server_jar_url = f"{config.BMCLAPI_URL}/version/{mc_version}/server"
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