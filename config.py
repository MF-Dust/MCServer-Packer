import os
import yaml
from pathlib import Path
from dotenv import load_dotenv
from utils.logger import log

class Config:
    def __init__(self):
        # --- 默认配置 ---
        self.defaults = {
            'download': {
                'concurrency': 16,
                'display_concurrency': 5,
                'retries': 3
            },
            'deearth': {
                'concurrency': 10
            },
            'installer': {
                'java_memory': '4G'
            }
        }
        self.settings = self.defaults.copy()
        self._load_from_file()

        # --- 环境变量和常量 ---
        load_dotenv()
        self.CURSEFORGE_API_KEY = os.getenv("CURSEFORGE_API_KEY", "$2a$10$bL4bIL5pUWqfcO7KQtnMReakwtfHbNKh6v1uTpKlzhwoueEJQnPnm")
        self.use_mirror = True  # 会在 main.py 中被用户选择覆盖
        self.is_development = os.getenv("DEVELOPMENT") is not None
        
        self.unzip_path = Path.cwd() / "instance"
        self.DEEARTH_CACHE_PATH = self.unzip_path / ".deearth_cache.json"

    def _load_from_file(self):
        config_path = Path.cwd() / "config.yaml"
        if config_path.exists():
            try:
                with open(config_path, 'r', encoding='utf-8') as f:
                    user_config = yaml.safe_load(f)
                
                # 深度合并
                for key, value in user_config.items():
                    if isinstance(value, dict) and key in self.settings:
                        self.settings[key].update(value)
                    else:
                        self.settings[key] = value
                log.info("已从 config.yaml 加载自定义配置。")
            except Exception as e:
                log.error(f"加载 config.yaml 失败: {e}")

    @property
    def download_concurrency(self):
        return self.settings['download']['concurrency']

    @property
    def display_concurrency(self):
        return self.settings['download']['display_concurrency']

    @property
    def download_retries(self):
        return self.settings['download']['retries']
        
    @property
    def deearth_concurrency(self):
        return self.settings['deearth']['concurrency']
        
    @property
    def java_memory(self):
        return self.settings['installer']['java_memory']


# 创建一个全局配置实例
config = Config()