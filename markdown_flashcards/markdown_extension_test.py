from rich.console import Console, JustifyMethod  # type: ignore
from rich.style import Style
from rich.markdown import Markdown  # type: ignore
from markdown_it import MarkdownIt


console = Console(force_terminal=True)
# hmm, in de Rich code wordt parser in initializer aangemaakt
# en meteen daarna wordt hij ook gebruikt
# kan ik de initializer van Markdown monkey patchen?
# dus class variable instellen heeft geen zin


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
    # parser = MarkdownIt()
    self.markup = markup
    self.parsed = parser.parse(markup)
    self.code_theme = code_theme
    self.justify = justify
    self.style = style
    self.hyperlinks = hyperlinks
    self.inline_code_lexer = inline_code_lexer
    self.inline_code_theme = inline_code_theme or code_theme


# TODO: check whether I can produce a custom element and control its rendering
# if so, I can do cloze deletions and answers

# replace with version without tested features
Markdown.__init__ = patched_init
displayed_text = Markdown("""# Test
Zie ik het effect van enable (of gebrek daaraan)?
Tekst met ~~strikethrough~~

| hoofding 1 | hoofding 2 |
|------------|------------|
| data 1     | data 2     |
""")
console.print(displayed_text)
