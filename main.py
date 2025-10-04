import asyncio
import json
import logging
import shutil
import sys
import zipfile
from pathlib import Path

import questionary

import config
from deearth import deearth_main
from downloader import async_client
from platforms import get_platform
from server_installer import install_server, create_launch_scripts
from utils.logger import log, console

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
    instance_dir = config.unzip_path / zip_name
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
    init_dir()

    try:
        config.use_mirror = await questionary.confirm("是否优先使用镜像源 (推荐)?", default=True).ask_async()
    except Exception:
        answer = input("是否优先使用镜像源 (推荐)? (Y/n) ")
        config.use_mirror = not answer.lower().startswith('n')


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
    if not config.unzip_path.exists():
        (config.unzip_path / ".rubbish").mkdir(parents=True, exist_ok=True)


if __name__ == "__main__":
    try:
        asyncio.run(cli_main())
    except KeyboardInterrupt:
        log.info("\n操作被用户中断")
    except Exception as e:
        log.error(f"发生未处理的错误: {e}")
        console.print_exception(show_locals=False)