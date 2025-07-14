from rich.console import Console  # type: ignore
from rich.markdown import Markdown  # type: ignore


console = Console()
displayed_text = Markdown("""# Test
Rendert het laatste woord van deze vraag in het {blue|blauw}?""")
console.print(displayed_text)
