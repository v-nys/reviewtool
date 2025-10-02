import click  # type: ignore
from rich.table import Table  # type: ignore
import math
from rich.prompt import Confirm, IntPrompt  # type: ignore
import sqlite3
from pathlib import Path
from rich.console import Console  # type: ignore
from rich.markdown import Markdown  # type: ignore
import re
from enum import Enum
from queue import PriorityQueue, Empty
from abc import ABC
from functools import total_ordering
import datetime
from abc import abstractmethod
import networkx as nx  # type: ignore
import frontmatter  # type: ignore
import logging
import coloredlogs  # type: ignore
import pathlib
import os
import yaml
import sys

from textual_image.renderable import Image  # type: ignore
from typing import List, Set, Union


class CardTypes(str, Enum):
    NORMAL = "normal"
    CLOZE = "cloze"


START_TIME = datetime.datetime.now()
TODAY = START_TIME.date()
MIDNIGHT = datetime.time(0, 0, 0)
ONE_DAY = datetime.timedelta(days=1)
ANSWER_OPTIONS = ["Unable to answer", "Hard", "Easy", "Very easy"]
LOGGER = logging.getLogger(__name__)
coloredlogs.install(level="WARNING", logger=LOGGER)
START_OF_OCCLUSION_REGEX = re.compile(
    r"£{c(?P<occlusion_number>\d+):(?P<start_of_occluded_text>)"
)  # e.g. £{c2: without the }, extra } to avoid confusing the editor in which you are viewing this
MD_IMG_REGEX = re.compile(r"!\[[^\]]*\]\((?P<path>[^\)]*)\)")
NORMAL_CARD_REGEX = re.compile(
    r"(?P<front>.*)\n---\n(?P<back>.*)",
    flags=re.DOTALL,
)
CLOZE_REGEX = re.compile(
    r"(?P<front>.*)",
    flags=re.DOTALL,
)
SANITIZED_CHARACTERS = re.compile(r"[^a-zA-Z0-9\s]")

Confirm.prompt_suffix = ""

application_data_directory = pathlib.Path("~/.markdown-flashcards/").expanduser()
try:
    os.mkdir(application_data_directory)
except FileExistsError:
    pass

logging.basicConfig(
    level=logging.INFO,
    filemode="w",
    filename=application_data_directory / "markdown-flashcards.log",
    force=True,
)


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
            return remaining_text[: index + 1]
    return None


def round_timedelta_days_up(timedelta):
    # this is a bit trickier than it seems
    # e.g. a timedelta of 3 weeks and 2 days has 0 for the "milliseconds" property
    # total_seconds() does work
    # but it's only for seconds
    # but, to be fair, we don't need millisecond level accuracy here
    days_in_timedelta = timedelta.total_seconds() / 60 / 60 / 24
    if (days_in_timedelta).is_integer():
        # make a (rough) copy because the other branch definitely returns a new object
        # would be inconsistent to just change the object here
        return datetime.timedelta(seconds=timedelta.total_seconds())
    else:
        return datetime.timedelta(seconds=math.ceil(days_in_timedelta) * 24 * 60 * 60)


def substitute_images_in_md_text(
    directory: Path, relative_card_path: Path, source: str
) -> List[Union[Markdown, Image]]:
    # would be nicer if this was actually based on parse tree
    # but this'll work fine in practice
    document_path = directory / relative_card_path
    segments = MD_IMG_REGEX.split(source)
    replacements = []
    for index, segment in enumerate(segments, start=0):
        LOGGER.debug(f"Processing segment {segment}")
        if index % 2:
            image_path = segment
            if image_path.startswith("./") or image_path.startswith("../"):
                absolute_image_path = (document_path.parent / image_path).resolve()
            elif image_path.startswith("/"):
                absolute_image_path = Path(image_path).resolve()
            else:
                absolute_image_path = (directory / image_path).resolve()
            replacements.append(Image(absolute_image_path))
        else:
            replacements.append(Markdown(segment))
    return replacements


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
    def due_date(self) -> datetime.datetime:
        if not (
            self.last_review_date and self.confidence_score and self.previous_time_delta
        ):
            return START_TIME
        else:
            match self.confidence_score:
                case 1:
                    return START_TIME

                case 2:
                    return max(
                        self.last_review_date + datetime.timedelta(minutes=3),
                        self.last_review_date + (self.previous_time_delta * 0.8),
                    )
                case 3:
                    # always postpone until at least tomorrow
                    # otherwise, we might still have to review (multiple times) today if gap was small
                    return min(
                        (
                            self.last_review_date + (self.previous_time_delta * 1.25)
                            if self.previous_time_delta >= datetime.timedelta(days=4)
                            # it may seem odd to use last_review_date instead of TODAY here
                            # but it makes sense
                            # TODAY is dependent on when we are running the program
                            # so due dates would *always* end up being in the future
                            # and last_review_date is set when we practice a card
                            # so it's the "today" of when we last viewed the card
                            else datetime.datetime.combine(
                                self.last_review_date.date()
                                # so if it's been less than a day, add at least one day
                                # so if it's been less than two, add at least two
                                # eventually, we'll round up to 4 and hit the exponential part
                                + round_timedelta_days_up(self.previous_time_delta),
                                MIDNIGHT,
                            )
                        ),
                        self.last_review_date + datetime.timedelta(days=365 // 2),
                    )
                case 4:
                    return min(
                        (
                            self.last_review_date + (self.previous_time_delta * 2)
                            if self.previous_time_delta >= datetime.timedelta(days=1)
                            else datetime.datetime.combine(
                                self.last_review_date.date() + (ONE_DAY * 2), MIDNIGHT
                            )
                        ),
                        self.last_review_date + datetime.timedelta(days=365),
                    )
        assert False, "Cases are exhaustive."

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
    def get_displayed_question(
        self, topics_directory: Path
    ) -> List[Union[Markdown, Image]]:
        return NotImplemented

    @abstractmethod
    def get_displayed_answer(
        self, topics_directory: Path
    ) -> List[Union[Markdown, Image]]:
        return NotImplemented

    @abstractmethod
    def update_with_confidence_score(self, score):
        return NotImplemented

    @abstractmethod
    def upsert(cursor):
        return NotImplemented


def show_and_evaluate_queue_item(
    queue_item: Card, console, directory, priority_queue, cur, con
):
    LOGGER.info(queue_item)
    LOGGER.info(f"Due {queue_item.due_date}")
    if queue_item.is_due_today:
        console.print(f"(From {str(Path(queue_item.relative_path).parent)})")
        if queue_item.last_review_date:
            console.print(
                f"(Last reviewed {queue_item.last_review_date.isoformat()}, previous time delta was {queue_item.previous_time_delta}, confidence score was {queue_item.confidence_score})"
            )
        components = queue_item.get_displayed_question(directory)
        for component in components:
            LOGGER.debug(f"Dit is de component: {component}")
            console.print(component)
        console.print("")
        Confirm.ask(
            "Press ENTER to display the answer",
            default=True,
            show_default=False,
            show_choices=False,
        )
        components = queue_item.get_displayed_answer(directory)
        for component in components:
            console.print(component)
        table = Table(title=None)
        table.add_column("Number", justify="right")
        table.add_column("Option", justify="left")
        for index, option in enumerate(ANSWER_OPTIONS, start=1):
            table.add_row(str(index), option)
        console.print("")

        console.print(table)
        confidence_score = IntPrompt.ask(
            "Select an option",
            choices=[str(i) for i in range(1, len(ANSWER_OPTIONS) + 1)],
        )
        updated_version = queue_item.update_with_confidence_score(confidence_score)
        console.print(f"Due date for review: {updated_version.due_date}")
        LOGGER.info(
            f"Due date for review of {queue_item.relative_path}: {updated_version.due_date}"
        )
        priority_queue.put(updated_version)
        updated_version.upsert(cur)
        con.commit()
        console.print("")
        # console.clear()


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

    def get_displayed_question(self, topics_directory):
        return substitute_images_in_md_text(
            topics_directory, self.relative_path, self.front
        )

    def get_displayed_answer(self, topics_directory):
        return substitute_images_in_md_text(
            topics_directory, self.relative_path, self.back
        )

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
                self.previous_time_delta.total_seconds()
                if self.previous_time_delta
                else None,
                self.last_review_date.isoformat() if self.last_review_date else None,
                self.confidence_score,
                self.previous_time_delta.total_seconds()
                if self.previous_time_delta
                else None,
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

    def get_displayed_question(self, topics_directory):
        LOGGER.debug(
            f"Displaying a Cloze card. Variant number is {self.variant_number}. Type of self.variant_number is {type(self.variant_number)}"
        )
        start_of_occlusion_matches = START_OF_OCCLUSION_REGEX.finditer(self.front)
        LOGGER.debug(f"This is the front: {self.front}")
        replacement_pairs = []
        for match in start_of_occlusion_matches:
            LOGGER.debug(match)
            LOGGER.debug(f"occlusion number group: {match.group('occlusion_number')}")
            start_index = match.start("start_of_occluded_text")
            until_curly_bracket = splice_until_matching_curly_bracket(
                self.front[start_index:]
            )
            if not until_curly_bracket:
                return [Markdown("Error: mismatched opening occlusion")]
            elif int(match.group("occlusion_number")) == self.variant_number:
                LOGGER.debug("Occluding.")
                whole_occlusion = match.group(0) + until_curly_bracket
                replacement_pairs.append((whole_occlusion, "[...]"))
            else:
                LOGGER.debug("Not occluding.")
                whole_occlusion = match.group(0) + until_curly_bracket
                replacement_pairs.append((whole_occlusion, until_curly_bracket[:-1]))
        displayed = str(self.front)
        LOGGER.debug(f"Replacement pairs are: {replacement_pairs}")
        for replacee, replacer in replacement_pairs:
            displayed = displayed.replace(replacee, replacer)
        return substitute_images_in_md_text(
            topics_directory, self.relative_path, displayed
        )

    def get_displayed_answer(self, topics_directory):
        start_of_occlusion_matches = START_OF_OCCLUSION_REGEX.finditer(self.front)
        replacement_pairs = []
        for match in start_of_occlusion_matches:
            start_index = match.start("start_of_occluded_text")
            until_curly_bracket = splice_until_matching_curly_bracket(
                self.front[start_index:]
            )
            if not until_curly_bracket:
                return [Markdown("Error: mismatched opening occlusion")]
            else:
                whole_occlusion = match.group(0) + until_curly_bracket
                replacement_pairs.append((whole_occlusion, until_curly_bracket[:-1]))
        displayed = str(self.front)
        for replacee, replacer in replacement_pairs:
            displayed = displayed.replace(replacee, replacer)
        return substitute_images_in_md_text(
            topics_directory, self.relative_path, displayed
        )

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
                self.previous_time_delta.total_seconds()
                if self.previous_time_delta
                else None,
                self.last_review_date.isoformat() if self.last_review_date else None,
                self.confidence_score,
                self.previous_time_delta.total_seconds()
                if self.previous_time_delta
                else None,
            ),
        )


def normalize_dependency_path(directory: Path, card_path: Path, dependency: str) -> str:
    if not (dependency.startswith("./") or dependency.startswith("../")):
        return dependency
    elif dependency.startswith("/"):
        LOGGER.error(
            f"Dependency of {card_path} starts with a slash. This suggests an absolute path, which is not used here. Only a path relative to the cards folder or relative to the card itself."
        )
        sys.exit(1)
    else:
        dependency_relative_to_card = (card_path.parent / dependency).resolve()
        return str(dependency_relative_to_card.relative_to(directory, walk_up=True))


def build_dependent_to_dependency_graph(
    card_paths, directory, relative_card_paths
) -> nx.DiGraph:
    """Construct a dependency graph representing how cards are related.

    A card somewhere under `directory` can have dependencies not under `directory`.
    """
    # need to collect these in first pass because each card specifies all its dependencies
    # that allows __lt__ and __eq__ to be implemented
    dependency_graph = nx.DiGraph()
    for card_path in card_paths:
        LOGGER.debug(f"Adding {card_path} to dependency graph.")
        has_valid_frontmatter = frontmatter.check(card_path)
        if has_valid_frontmatter:
            card = frontmatter.load(card_path)
            card_relative_path = card_path.relative_to(directory, walk_up=True)
            dependency_graph.add_node(str(card_relative_path))
            for dependency in card.get("dependencies", []):
                dependency = normalize_dependency_path(directory, card_path, dependency)
                # FIXME: this is wrong
                # a dependency outside relative_card_paths should be fine, as long as the file exists?
                # come back to this later
                if dependency not in relative_card_paths:
                    LOGGER.error(
                        f"{dependency} is mentioned as a dependency of {card_relative_path}, but there is no Markdown file with this path (relative to the overall cards directory. Ignoring the dependency (and potential transitive dependencies)."
                    )
                else:
                    dependency_graph.add_node(str(dependency))
                    dependency_graph.add_edge(str(card_relative_path), str(dependency))
        else:
            LOGGER.error(f"Card at {card_path} has invalid frontmatter.")
    LOGGER.debug(f"Dependency graph: {dependency_graph}")
    LOGGER.debug(f"Nodes: {dependency_graph.nodes}")
    return dependency_graph


def add_cards_to_priority_queue(
    db_entries_for_card,
    card_path,
    relative_path,
    dependency_graph,
    priority_queue,
    cur,
    con,
):
    # want to access via index but also don't want duplicates, so list({...})
    card_types = list({db_entry[0] for db_entry in db_entries_for_card})
    if len(card_types) > 1:
        print(
            f"Database specifies multiple types for the card {card_path}. This is not allowed."
        )
        return
    elif len(db_entries_for_card) > 1 and card_types[0] == CardTypes.NORMAL:
        print(
            f"Card {card_path} is a regular card according to DB, but there are multiple records for it. Only in the case of cloze variants can there be multiple entries for the same card."
        )
    else:
        try:
            db_entry = db_entries_for_card[0]
            card_type_according_to_db = card_types.pop()
        except IndexError:
            db_entry = None
            card_type_according_to_db = None
        LOGGER.info(f"DB entry for single card type: {db_entry}")
        with open(card_path) as fh:
            raw_text = fh.read()
            if frontmatter.checks(raw_text):
                frontmatter_card = frontmatter.loads(raw_text)
                normal_card_match = NORMAL_CARD_REGEX.match(frontmatter_card.content)
                cloze_match = CLOZE_REGEX.match(frontmatter_card.content)
                if normal_card_match:
                    if (
                        card_type_according_to_db
                        and card_type_according_to_db != CardTypes.NORMAL
                    ):
                        LOGGER.error(
                            f"Card at {card_path} should be a regular flash card according to DB but does not match the regular expression for a regular flash card. "
                            + "It will not go into the queue. You should either fix the card or remove the database entry."
                        )
                        return
                    card = NormalCard(
                        relative_path,
                        frontmatter_card.get("tags", []),
                        nx.descendants(dependency_graph, relative_path),
                        db_entry
                        and db_entry[2]
                        and datetime.datetime.fromisoformat(db_entry[2]),
                        db_entry and db_entry[3] and int(db_entry[3]),
                        db_entry
                        and db_entry[4]
                        and datetime.timedelta(seconds=int(float(db_entry[4]))),
                        normal_card_match.group("front"),
                        normal_card_match.group("back"),
                    )
                    if not db_entries_for_card:
                        card.upsert(cur)
                        con.commit()
                    priority_queue.put(card)
                elif cloze_match:
                    if (
                        card_type_according_to_db
                        and card_type_according_to_db != CardTypes.CLOZE
                    ):
                        LOGGER.error(
                            f"Card at {card_path} should be a cloze card according to DB but does not match the regular expression for a cloze card. "
                            + "It will not go into the queue. You should either fix the card or remove the database entry."
                        )
                        return
                    start_of_occlusion_matches = list(
                        START_OF_OCCLUSION_REGEX.finditer(raw_text)
                    )
                    occlusion_numbers_in_file = {
                        int(occlusion_match.group("occlusion_number"))
                        for occlusion_match in start_of_occlusion_matches
                    }
                    occlusion_numbers_in_db = db_entries_for_card and {
                        int(db_entry[1]) for db_entry in db_entries_for_card
                    }
                    if (
                        not db_entries_for_card
                        or occlusion_numbers_in_file == occlusion_numbers_in_db
                    ):
                        for db_entry, occlusion_number_in_file in zip(
                            sorted(
                                db_entries_for_card
                                or [None] * len(occlusion_numbers_in_file),
                                key=lambda maybe_entry: (
                                    maybe_entry and int(db_entry[1])
                                )
                                or 0,
                            ),
                            sorted(list(occlusion_numbers_in_file), key=int),
                        ):
                            card = ClozeVariant(
                                relative_path,
                                frontmatter_card.get("tags", []),
                                nx.descendants(dependency_graph, relative_path),
                                db_entry
                                and db_entry[2]
                                and datetime.datetime.fromisoformat(db_entry[2]),
                                db_entry and db_entry[3] and int(db_entry[3]),
                                db_entry
                                and db_entry[4]
                                and datetime.timedelta(seconds=int(float(db_entry[4]))),
                                cloze_match.group("front"),
                                occlusion_number_in_file,
                            )
                            priority_queue.put(card)
                            if not db_entries_for_card:
                                card.upsert(cur)
                                con.commit()

                    else:
                        LOGGER.error(
                            f"Card at {card_path} does not use the same occlusion numbers {occlusion_numbers_in_db} that are mentioned in the database. "
                            + "Its variants will not go into the queue. "
                            + "You should update the database records or change the file to use precisely the aforementioned occlusion numbers."
                        )

                else:
                    LOGGER.error(
                        f"Card at {card_path} should be a cloze card according to DB but does not match the regular expression for a cloze card. "
                        + "It will not go into the queue. You should either fix the card or remove the database entries for its variants."
                    )
            else:
                LOGGER.error(f"Card at {card_path} has invalid frontmatter")


def add_card_to_priority_queue_and_maybe_db(
    card_path,
    directory,
    cur,
    dependency_graph,
    priority_queue,
    con,
):
    relative_path = str(card_path.relative_to(directory, walk_up=True))
    cur.execute(
        "select CardType, ClozeVariant, LastReviewDate, ConfidenceScore, PreviousTimeDelta from Cards where RelativePath=?",
        (relative_path,),
    )
    # plural due to Cloze variants
    db_entries_for_card = list(cur.fetchall())
    add_cards_to_priority_queue(
        db_entries_for_card,
        card_path,
        relative_path,
        dependency_graph,
        priority_queue,
        cur,
        con,
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
        path_type=Path,
    ),
)
@click.argument("decks", required=False, nargs=-1, type=str)
def quiz(directory, decks):
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
            print(
                f"Path is mentioned in DB but lacks a Markdown file counterpart: {relative_path}"
            )
            should_delete = Confirm.ask("Delete entry from database?")
            if should_delete:
                cur.execute(
                    """delete from Cards where RelativePath=?""", (relative_path,)
                )
                con.commit()

    card_paths: Set[Path] = set(directory.glob("**/*.md"))
    relative_card_paths: List[str] = [
        str(card_path.relative_to(directory, walk_up=True)) for card_path in card_paths
    ]
    LOGGER.debug(f"Card paths: {card_paths}")
    dependent_to_dependency_graph = build_dependent_to_dependency_graph(
        card_paths, directory, relative_card_paths
    )

    unreviewed_ids = set()
    for node in dependent_to_dependency_graph.nodes:
        if not decks:
            pass
        elif any(
            (node.startswith(f"{subfolder_prefix}/") for subfolder_prefix in decks)
        ):
            pass
        elif any(
            (
                dependent.startswith(f"{subfolder_prefix}/")
                for subfolder_prefix in decks
                for dependent in dependent_to_dependency_graph.predecessors(node)
            )
        ):
            pass
        else:
            unreviewed_ids.add(node)
    for unreviewed_id in unreviewed_ids:
        dependent_to_dependency_graph.remove_node(unreviewed_id)

    priority_queue = PriorityQueue()
    for node_id in dependent_to_dependency_graph.nodes:
        add_card_to_priority_queue_and_maybe_db(
            Path(directory) / node_id,  # card_path,
            directory,
            cur,
            dependent_to_dependency_graph,
            priority_queue,
            con,
        )
    queue_item = priority_queue.get()
    console = Console()
    while queue_item:
        show_and_evaluate_queue_item(
            queue_item, console, directory, priority_queue, cur, con
        )
        try:
            queue_item = priority_queue.get(block=False)
        except Empty:
            cur.close()
            break


@click.command()
@click.argument(
    "directory",
    required=True,
    type=click.Path(
        exists=True,
        file_okay=False,
        dir_okay=True,
        readable=True,
        path_type=Path,
    ),
)
@click.argument("decks", required=False, nargs=-1, type=str)
def organize(directory, decks: tuple[str, ...]):
    card_paths: Set[Path] = set(directory.glob("**/*.md"))
    relative_card_paths: List[str] = [
        str(card_path.relative_to(directory, walk_up=True)) for card_path in card_paths
    ]
    from flask import Flask, render_template, redirect, request, url_for

    app = Flask(__name__)

    @app.route("/add-dependency", methods=["POST"])
    def add_edge():
        dependent = request.form["edge-introduction-dependent"]
        dependent_path = directory / dependent
        dependency = request.form["edge-introduction-dependency"]
        LOGGER.debug(f"Should add edge from {dependency} to {dependent}")
        dependent_body = (directory / dependent).read_text()
        dependent_card = frontmatter.loads(dependent_body)
        dependent_content = dependent_card.content
        dependent_metadata = dependent_card.metadata
        if "dependencies" in dependent_metadata:
            dependency_card_path = directory / dependency
            dependent_card_path = directory / dependent
            dependency_card_relative_to_dependent_card_folder = str(
                dependency_card_path.relative_to(
                    dependent_card_path.parent, walk_up=True
                )
            )
            if not (
                dependency_card_relative_to_dependent_card_folder.startswith("./")
                or dependency_card_relative_to_dependent_card_folder.startswith("..")
            ):
                dependency_card_relative_to_dependent_card_folder = (
                    "./" + dependency_card_relative_to_dependent_card_folder
                )
            dependent_metadata["dependencies"] += [
                dependency_card_relative_to_dependent_card_folder
            ]
        rewritten_card = f"""---
{yaml.dump(dependent_metadata)}---
{dependent_content}
"""
        with open(dependent_path, mode="w") as fh:
            fh.write(rewritten_card)
        return redirect(url_for("view_dependency_graph"))

    @app.route("/delete-edge", methods=["POST"])
    def delete_edge():
        dependent = request.form["edge-deletion-dependent"]
        dependent_path = directory / dependent
        dependency = request.form["edge-deletion-dependency"]
        LOGGER.debug(f"Should delete edge from {dependency} to {dependent}")
        dependent_body = (directory / dependent).read_text()
        dependent_card = frontmatter.loads(dependent_body)
        dependent_content = dependent_card.content
        dependent_metadata = dependent_card.metadata
        if "dependencies" in dependent_metadata:
            dependent_metadata["dependencies"] = [
                d
                for d in dependent_metadata["dependencies"]
                if normalize_dependency_path(directory, dependent_path, d) != dependency
            ]
        rewritten_card = f"""---
{yaml.dump(dependent_metadata)}---
{dependent_content}
"""
        with open(dependent_path, mode="w") as fh:
            fh.write(rewritten_card)
        return redirect(url_for("view_dependency_graph"))

    @app.route("/")
    def view_dependency_graph():
        dependency_to_dependent_graph = build_dependent_to_dependency_graph(
            card_paths, directory, relative_card_paths
        ).reverse()

        unreviewed_ids = set()
        # filter out anything not under review
        for node in dependency_to_dependent_graph.nodes:
            if not decks:
                pass
            elif any(
                (node.startswith(f"{subfolder_prefix}/") for subfolder_prefix in decks)
            ):
                pass
            elif any(
                (
                    dependent.startswith(f"{subfolder_prefix}/")
                    for subfolder_prefix in decks
                    for dependent in dependency_to_dependent_graph.successors(node)
                )
            ):
                pass
            else:
                unreviewed_ids.add(node)
        LOGGER.info(f"Unreviewed IDs: {list(unreviewed_ids)}")
        for unreviewed_id in unreviewed_ids:
            dependency_to_dependent_graph.remove_node(unreviewed_id)
        LOGGER.info(f"Nodes: {list(dependency_to_dependent_graph.nodes)}")

        pydot_graph = nx.nx_pydot.to_pydot(dependency_to_dependent_graph)
        # RL as edges are from dependent to dependency
        # makes more sense visually to read from dependency to dependent
        pydot_graph.set_rankdir("LR")

        for node in dependency_to_dependent_graph.nodes():
            body = (directory / node).read_text()
            frontmatter_card = frontmatter.loads(body)
            content = frontmatter_card.content
            normal_card_match = NORMAL_CARD_REGEX.match(content)
            if normal_card_match:
                body2 = normal_card_match.group("front")
            else:
                body2 = content
            # TODO: improve sanitization
            body3 = SANITIZED_CHARACTERS.sub("", body2).strip()
            gv_label = frontmatter_card.get("graphviz_label", body3)
            pydot_graph.get_node(node)[0].set_tooltip(content)
            pydot_graph.get_node(node)[0].set_label(gv_label)
            pydot_graph.get_node(node)[0].set_shape("box")
            pydot_graph.get_node(node)[0].set_margin("0")
            pydot_graph.get_node(node)[0].set_width("0")
            pydot_graph.get_node(node)[0].set_nojustify("true")
        for edge in dependency_to_dependent_graph.edges():
            pydot_graph.get_edge(edge[0], edge[1])[0].set_label("❌")
        pydot_graph.write_dot("/home/vincentn/graphoutput.gv")
        svg = pydot_graph.create_svg().decode("utf-8")
        return render_template("dependency_graph.html", svg=svg)

    app.run()


if __name__ == "__main__":
    quiz()
