import click  # type: ignore
from rich.table import Table  # type: ignore
from rich.prompt import Confirm, IntPrompt  # type: ignore
import sqlite3
import pathlib
from rich.console import Console  # type: ignore
from rich.markdown import Markdown  # type: ignore
import re
from enum import Enum
from queue import PriorityQueue
from abc import ABC
from functools import total_ordering
import datetime
from abc import abstractmethod
import networkx as nx  # type: ignore
from frontmatter import Frontmatter  # type: ignore
import logging


class CardTypes(str, Enum):
    NORMAL = "normal"
    CLOZE = "cloze"


START_TIME = datetime.datetime.now()
TODAY = START_TIME.date()
ONE_DAY = datetime.timedelta(days=1)
ANSWER_OPTIONS = ["Unable to answer", "Hard", "Easy", "Very easy"]
LOGGER = logging.getLogger(__name__)
START_OF_OCCLUSION_REGEX = re.compile(r"£{c(?P<occlusion_number>\d+):(?P<start_of_occluded_text>)")  # e.g. £{c2: without the }, extra } to avoid confusing editor

logging.basicConfig(filename="markdown-flashcards.log", level=logging.INFO)


def splice_until_matching_curly_bracket(remaining_text):
    """
    Take text that follows an opening '{' and return the part until and including the matching '}'.

    If there is no match, return `None`.
    """
    opening_curly_brackets = 1
    for index, character in enumerate(remaining_text):
        match character:
            case "{":
                opening_curly_brackets += 1
            case "}":
                opening_curly_brackets -= 1
            case _other:
                pass
        if opening_curly_brackets == 0:
            return remaining_text[:index+1]
    return None

@total_ordering
class Card(ABC):
    @property
    def is_due_at_start(self):
        # not using a normal `is_due` because now() would be used in comparisons
        return self.due_date <= START_TIME

    @property
    def is_due_today(self):
        return self.due_date.date() <= TODAY

    @property
    def due_date(self):
        if not (
            self.last_review_date and self.confidence_score and self.previous_time_delta
        ):
            return START_TIME
        else:
            match self.confidence_score:
                case 1:
                    return START_TIME
                case 2:
                    return min(
                        START_TIME + datetime.timedelta(minutes=3),
                        START_TIME + (self.previous_time_delta * 0.8),
                    )
                case 3:
                    return min(
                        max(
                            datetime.datetime.combine(TODAY, datetime.time(0, 0))
                            + ONE_DAY,
                            START_TIME + (self.previous_time_delta * 1.25),
                        ),
                        datetime.datetime.combine(TODAY, datetime.time(0, 0))
                        + ONE_DAY * 365,
                    )
                case 4:
                    return min(
                        max(
                            datetime.datetime.combine(TODAY, datetime.time(0, 0))
                            + (ONE_DAY * 2),
                            START_TIME + (self.previous_time_delta * 2),
                        ),
                        datetime.datetime.combine(TODAY, datetime.time(0, 0))
                        + ONE_DAY * 365,
                    )

    def __init__(
        self,
        relative_path,
        tags,
        all_dependencies,
        last_review_date,
        confidence_score,
        previous_time_delta,
    ):
        self.relative_path = relative_path
        self.tags = tags
        self.all_dependencies = all_dependencies
        self.last_review_date = last_review_date
        self.confidence_score = confidence_score
        self.previous_time_delta = previous_time_delta

    def __eq__(self, other):
        if (
            self.relative_path in other.all_dependencies
            or other.relative_path in self.all_dependencies
        ):
            return False
        else:
            return self.due_date == other.due_date

    def __lt__(self, other):
        LOGGER.debug(f"Comparing {self.relative_path} and {other.relative_path}")
        if self.relative_path in other.all_dependencies:
            if self.is_due_today:
                return True
            else:
                return self.due_date <= other.due_date
        elif other.relative_path in self.all_dependencies:
            if other.is_due_today:
                return False
            else:
                return self.due_date < other.due_date
        else:
            return self.due_date < other.due_date

    @abstractmethod
    def get_displayed_question(self):
        return NotImplemented

    @abstractmethod
    def get_displayed_answer(self):
        return NotImplemented

    @abstractmethod
    def update_with_confidence_score(self, score):
        return NotImplemented

    @abstractmethod
    def upsert(cursor):
        return NotImplemented


class NormalCard(Card):
    def __init__(
        self,
        relative_path,
        tags,
        all_dependencies,
        last_review_date,
        confidence_score,
        previous_time_delta,
        front,
        back,
    ):
        super().__init__(
            relative_path,
            tags,
            all_dependencies,
            last_review_date,
            confidence_score,
            previous_time_delta,
        )
        self.front = front
        self.back = back

    def get_displayed_question(self):
        return Markdown(self.front)

    def get_displayed_answer(self):
        return Markdown(self.back)

    def update_with_confidence_score(self, score):
        now = datetime.datetime.now()
        return NormalCard(
            self.relative_path,
            self.tags,
            self.all_dependencies,
            now,
            score,
            now - self.last_review_date if self.last_review_date else now - START_TIME,
            self.front,
            self.back,
        )

    def upsert(self, cur):
        cur.execute(
            """insert into Cards(CardType, ClozeVariant, RelativePath, LastReviewDate, ConfidenceScore, PreviousTimeDelta) values (?, 0, ?, ?, ?, ?) on conflict(RelativePath, ClozeVariant) do update set LastReviewDate=?, ConfidenceScore=?,PreviousTimeDelta=?""",
            (
                CardTypes.NORMAL,
                self.relative_path,
                self.last_review_date.isoformat() if self.last_review_date else None,
                self.confidence_score,
                self.previous_time_delta.seconds if self.previous_time_delta else None,
                self.last_review_date.isoformat() if self.last_review_date else None,
                self.confidence_score,
                self.previous_time_delta.seconds if self.previous_time_delta else None,
            ),
        )


class ClozeVariant(Card):
    def __init__(
        self,
        relative_path,
        tags,
        all_dependencies,
        last_review_date,
        confidence_score,
        previous_time_delta,
        front,
        variant_number,
    ):
        super().__init__(
            relative_path,
            tags,
            all_dependencies,
            last_review_date,
            confidence_score,
            previous_time_delta,
        )
        self.front = front
        self.variant_number = variant_number

    def get_displayed_question(self):
        start_of_occlusion_matches = START_OF_OCCLUSION_REGEX.finditer(self.front)
        replacement_pairs = []
        for match in start_of_occlusion_matches:
            start_index = match.start("start_of_occluded_text")
            until_curly_bracket = splice_until_matching_curly_bracket(self.front[start_index:])
            if not until_curly_bracket:
                return Markdown("Error: mismatched opening occlusion")
            elif int(match.group("occlusion_number")) == self.variant_number:
                whole_occlusion = match.group(0) + until_curly_bracket
                replacement_pairs.append((whole_occlusion, "[...]"))
            else:
                whole_occlusion = match.group(0) + until_curly_bracket
                replacement_pairs.append((whole_occlusion, until_curly_bracket[:-1]))
        displayed = str(self.front)
        for (replacee, replacer) in replacement_pairs:
            displayed = displayed.replace(replacee, replacer)
        return Markdown(displayed)

    def get_displayed_answer(self):
        start_of_occlusion_matches = START_OF_OCCLUSION_REGEX.finditer(self.front)
        replacement_pairs = []
        for match in start_of_occlusion_matches:
            start_index = match.start("start_of_occluded_text")
            until_curly_bracket = splice_until_matching_curly_bracket(self.front[start_index:])
            if not until_curly_bracket:
                return Markdown("Error: mismatched opening occlusion")
            else:
                whole_occlusion = match.group(0) + until_curly_bracket
                replacement_pairs.append((whole_occlusion, until_curly_bracket[:-1]))
        displayed = str(self.front)
        for (replacee, replacer) in replacement_pairs:
            displayed = displayed.replace(replacee, replacer)
        return Markdown(displayed)

    def update_with_confidence_score(self, score):
        now = datetime.datetime.now()
        return ClozeVariant(
            self.relative_path,
            self.tags,
            self.all_dependencies,
            now,
            score,
            now - self.last_review_date if self.last_review_date else now - START_TIME,
            self.front,
            self.variant_number,
        )

    def upsert(self, cur):
        cur.execute(
            """insert into Cards(CardType, ClozeVariant, RelativePath, LastReviewDate, ConfidenceScore, PreviousTimeDelta) values (?, ?, ?, ?, ?, ?) on conflict(RelativePath, ClozeVariant) do update set LastReviewDate=?, ConfidenceScore=?,PreviousTimeDelta=?""",
            (
                CardTypes.CLOZE,
                self.variant_number,
                self.relative_path,
                self.last_review_date.isoformat() if self.last_review_date else None,
                self.confidence_score,
                self.previous_time_delta.seconds if self.previous_time_delta else None,
                self.last_review_date.isoformat() if self.last_review_date else None,
                self.confidence_score,
                self.previous_time_delta.seconds if self.previous_time_delta else None,
            ),
        )


@click.command()
@click.argument(
    "directory",
    required=True,
    type=click.Path(
        exists=True,
        file_okay=False,
        dir_okay=True,
        readable=True,
        path_type=pathlib.Path,
    ),
)
def quiz(directory):
    LOGGER.debug("Starting the quiz.")
    # TODO: consider getting rid of frontmatter part and using frontmatter library?
    normal_card_regex = re.compile(
        # actually, back should not contain ---
        r"---\n(?P<frontmatter>.*)\n---\n(?P<front>.*)\n---\n(?P<back>.*)",
        flags=re.DOTALL,
    )
    cloze_regex = re.compile(
        r"---\n(?P<frontmatter>.*)\n---\n(?P<front>.*)",
        flags=re.DOTALL,
    )
    con = sqlite3.connect(directory / "learning-history.db")
    cur = con.cursor()
    LOGGER.debug("Creating table if necessary.")
    cur.execute("""create table if not exists Cards(
        CardType text,
        ClozeVariant integer,
        RelativePath text,
        LastReviewDate text,
        ConfidenceScore integer,
        PreviousTimeDelta text,
        primary key (ClozeVariant, RelativePath)
        )""")

    LOGGER.debug("Checking for missing files.")
    relative_paths = cur.execute("select RelativePath from Cards")
    for (relative_path,) in relative_paths.fetchall():
        if not (directory / relative_path).exists():
            # TODO: prompt to delete from DB?
            print(
                f"Path is mentioned in DB but lacks a Markdown file counterpart: {relative_path}"
            )

    card_paths = list(directory.glob("**/*.md"))
    LOGGER.debug(f"Card paths: {card_paths}")

    # need to collect these in first pass because each card specifies all its dependencies
    # that allows __lt__ and __eq__ to be implemented
    dependency_graph = nx.DiGraph()
    for card_path in card_paths:
        LOGGER.debug(f"Adding {card_path} to dependency graph.")
        card = Frontmatter.read_file(card_path)
        card_relative_path = card_path.relative_to(directory)
        dependency_graph.add_node(str(card_relative_path))
        metadata = card["attributes"]
        for dependency in metadata.get("dependencies", []):
            dependency_graph.add_node(str(dependency))
            dependency_graph.add_edge(str(card_relative_path), str(dependency))
    LOGGER.debug(f"Dependency graph: {dependency_graph}")
    LOGGER.debug(f"Nodes: {dependency_graph.nodes}")

    priority_queue = PriorityQueue()
    for card_path in card_paths:
        relative_path = str(card_path.relative_to(directory))
        cur.execute(
            "select CardType, ClozeVariant, LastReviewDate, ConfidenceScore, PreviousTimeDelta from Cards where RelativePath=?",
            (relative_path,),
        )
        # plural due to Cloze variants
        db_entries_for_card = list(cur.fetchall())
        LOGGER.info(f"DB entries for card {card_path}: {db_entries_for_card}")
        if db_entries_for_card:
            card_types = {db_entry[0] for db_entry in db_entries_for_card}
            if len(card_types) > 1:
                print(
                    f"Database specifies multiple types for the card {card_path}. This is not allowed."
                )
                continue
            else:
                db_entry = db_entries_for_card[0]
                LOGGER.info(f"DB entry for single card type: {db_entry}")
                card_type = card_types.pop()
                with open(card_path) as fh:
                    raw_text = fh.read()
                    frontmatter_card = Frontmatter.read(raw_text)
                    metadata = frontmatter_card["attributes"]
                    if card_type == CardTypes.NORMAL:
                        normal_card_match = normal_card_regex.match(raw_text)
                        card = NormalCard(
                            relative_path,
                            metadata.get("tags", []),
                            nx.descendants(dependency_graph, relative_path),
                            datetime.datetime.fromisoformat(db_entry[2]),
                            int(db_entry[3]),
                            datetime.timedelta(seconds=int(db_entry[4])),
                            normal_card_match.group("front"),
                            normal_card_match.group("back"),
                        )
                        priority_queue.put(card)
                    elif card_type == CardTypes.CLOZE:
                        cloze_match = cloze_regex.match(raw_text)
                        # no need to check for occlusion matches
                        # assuming the card has not been changed
                        cards = [
                            ClozeVariant(
                                relative_path,
                                metadata.get("tags", []),
                                nx.descendants(dependency_graph, relative_path),
                                datetime.datetime.fromisoformat(db_entry[2]),
                                int(db_entry[3]),
                                datetime.timedelta(seconds=int(db_entry[4])),
                                cloze_match.group("front"),
                                db_entry[1],
                            )
                            for db_entry in db_entries_for_card
                        ]
                        for card in cards:
                            priority_queue.put(card)
        else:
            # no entries, so need to read card to create suitable entry
            with open(card_path) as fh:
                raw_text = fh.read()
                normal_card_match = normal_card_regex.match(raw_text)
                cloze_match = cloze_regex.match(raw_text)
                if normal_card_match:
                    frontmatter_card = Frontmatter.read(raw_text)
                    metadata = frontmatter_card["attributes"]
                    card = NormalCard(
                        relative_path,
                        metadata.get("tags", []),
                        nx.descendants(dependency_graph, relative_path),
                        None,
                        None,
                        None,
                        normal_card_match.group("front"),
                        normal_card_match.group("back"),
                    )
                    card.upsert(cur)
                    con.commit()
                    priority_queue.put(card)
                elif cloze_match:
                    # FIXME: this is repeated from earlier
                    frontmatter_card = Frontmatter.read(raw_text)
                    metadata = frontmatter_card["attributes"]
                    start_of_occlusion_matches = list(START_OF_OCCLUSION_REGEX.finditer(raw_text))
                    if not start_of_occlusion_matches:
                        print(
                            f"Cloze card {relative_path} does not contain any occlusions."
                        )
                        continue
                    else:
                        occlusion_numbers = {
                            occlusion_match.group("number")
                            for occlusion_match in start_of_occlusion_matches
                        }
                        cards = [
                            ClozeVariant(
                                relative_path,
                                metadata.get("tags", []),
                                nx.descendants(dependency_graph, relative_path),
                                None,
                                None,
                                None,
                                cloze_match.group("front"),
                                occlusion_number,
                            )
                            for occlusion_number in occlusion_numbers
                        ]
                        for card in cards:
                            priority_queue.put(card)
                            card.upsert(cur)
                        con.commit()
                else:
                    print(
                        f"Card {relative_path} does not match either normal or cloze pattern."
                    )
                    continue
    queue_item = priority_queue.get()
    console = Console()
    while queue_item and queue_item.is_due_today:
        LOGGER.info(queue_item)
        console.print(queue_item.get_displayed_question())
        Confirm.ask("Enter anything to continue", default=True)
        console.print(queue_item.get_displayed_answer())
        table = Table(title="Options")
        table.add_column("Number", justify="right")
        table.add_column("Option", justify="left")
        for index, option in enumerate(ANSWER_OPTIONS, start=1):
            table.add_row(str(index), option)
        console.print(table)
        confidence_score = IntPrompt.ask(
            "Select an option",
            choices=[str(i) for i in range(1, len(ANSWER_OPTIONS) + 1)],
        )
        updated_version = queue_item.update_with_confidence_score(confidence_score)
        priority_queue.put(updated_version)
        updated_version.upsert(cur)
        con.commit()
        queue_item = priority_queue.get()
    # TODO: can this be more of a "finally" thing?
    cur.close()


if __name__ == "__main__":
    quiz()
