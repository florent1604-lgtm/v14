
import io
from importlib.metadata import version
print("rich", version("rich"))
from rich.console import Console
c = Console(file=io.StringIO(), width=80)
c.print("[yellow]Note: '0.0.0.128' is missing a scheme. Ollama-serve typically expects a URL like http://<host>:11434/v1.[/yellow]")
print(repr(c.file.getvalue()))
c2 = Console(file=io.StringIO(), width=80, highlight=False)
c2.print("[yellow]Note: like http://<host>:11434/v1.[/yellow]")
print(repr(c2.file.getvalue()))
