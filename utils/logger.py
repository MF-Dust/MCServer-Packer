import logging
from rich.console import Console
from rich.logging import RichHandler

console = Console()

logging.basicConfig(
    level="INFO",
    format="%(message)s",
    datefmt="[%X]",
    handlers=[RichHandler(console=console, rich_tracebacks=True, show_path=False)],
)

log = logging.getLogger("rich")
logging.getLogger("httpx").setLevel(logging.WARNING)