from rich.console import Console  # type: ignore
from rich.markdown import Markdown  # type: ignore
from markdown_it import MarkdownIt

console = Console(force_terminal=True)
Markdown.parser = MarkdownIt().enable("strikethrough").enable("table")
displayed_text = Markdown("""# Test
Zie ik het effect van enable (of gebrek daaraan)?
Tekst met ~~strikethrough~~

| hoofding 1 | hoofding 2 |
|------------|------------|
| data 1     | data 2     |
""")
# nee, gewoon includen in string zal ook niet gaat
# Python ziet de escapesequentie, de terminal niet
# dus zou waarschijnlijk raw characters moeten outputten om dit te laten werken
console.print(displayed_text)
