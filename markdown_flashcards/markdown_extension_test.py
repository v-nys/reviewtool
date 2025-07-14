from rich.console import Console  # type: ignore
from rich.markdown import Markdown  # type: ignore
from markdown_it import MarkdownIt

console = Console(force_terminal=True)
Markdown.parser = MarkdownIt()
displayed_text = Markdown("""# Test
Zie ik het effect van enable (of gebrek daaraan)?
Tekst met ~~strikethrough~~

| hoofding 1 | hoofding 2 |
|------------|------------|
| data 1     | data 2     |
""")
# zou geen rendering van strikethrough en table verwachten...
console.print(displayed_text)
