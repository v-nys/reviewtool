from rich.console import Console, JustifyMethod  # type: ignore
from rich.style import Style  # type: ignore
from rich.markdown import Markdown  # type: ignore
from markdown_it import MarkdownIt  # type: ignore
from rich import default_styles  # type: ignore

# checken of deze patch zichtbaar gevolg heeft
default_styles.DEFAULT_STYLES["em"] = Style(italic=True, color="red")
default_styles.DEFAULT_STYLES["emph"] = Style(italic=True, color="red")
# blijkbaar niet...
# zou moeten uitzoeken hoe imports hier eigenlijk verlopen
# patchen van __init__ is waarschijnlijk simpeler omdat het ook *hier* is dat we de initializer gebruiken
# patchen van de styles


console = Console(force_terminal=True)


def patched_init(
    self,
    markup: str,
    code_theme: str = "monokai",
    justify: JustifyMethod | None = None,
    style: str | Style = "none",
    hyperlinks: bool = True,
    inline_code_lexer: str | None = None,
    inline_code_theme: str | None = None,
) -> None:
    # original
    parser = MarkdownIt().enable("strikethrough").enable("table")
    self.markup = markup
    self.parsed = parser.parse(markup)
    self.code_theme = code_theme
    self.justify = justify
    self.style = style
    self.hyperlinks = hyperlinks
    self.inline_code_lexer = inline_code_lexer
    self.inline_code_theme = inline_code_theme or code_theme


Markdown.__init__ = patched_init

# als ik een bepaalde tag kan restylen, is het eigenlijk oké
# want iets zoals mark of ins/del porten van JS is goed te doen
# dus ik kan die tags herbruiken

# elementen hebben een on_enter en on_leave die de context meekrijgt
# en de context heeft een "style stack"
# dus als ik een nieuw element definieer en dan die callbacks style laat pushen/poppen...
displayed_text = Markdown("""
Kan ik misschien eerst *em* tekst kleuren?
""")
console.print(displayed_text)
