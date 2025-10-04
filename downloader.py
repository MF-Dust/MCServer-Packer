import asyncio
from pathlib import Path
from typing import List, Optional, Tuple

import httpx
from rich.progress import (
    Progress, SpinnerColumn, BarColumn, TextColumn, TimeElapsedColumn,
    DownloadColumn, TransferSpeedColumn
)

from utils.logger import log, console
import config

# --- 异步 HTTP 客户端 ---
async_client = httpx.AsyncClient(
    headers={"User-Agent": "DeEarthX", "x-api-key": config.CURSEFORGE_API_KEY},
    follow_redirects=True,
    timeout=60.0,
)

# --- 核心下载逻辑 ---
async def fast_download(download_data: List[Tuple[str, Path, Optional[int]]], desc: str):
    """使用 Rich Progress，最多显示 5 个并发下载"""

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
            if config.use_mirror and not is_fallback and (url.startswith(config.CF_MIRROR_URL) or url.startswith(config.MR_MIRROR_URL)):
                official_url = ""
                if url.startswith(config.MR_MIRROR_URL):
                    official_url = url.replace(config.MR_MIRROR_URL, "https://cdn.modrinth.com")
                elif url.startswith(config.CF_MIRROR_URL + "/curseforge"):
                    official_url = url.replace(config.CF_MIRROR_URL, "https://edge.forgecdn.net")

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