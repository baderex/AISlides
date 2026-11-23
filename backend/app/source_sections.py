"""Select citable passages from complete retained source text."""
import re
from dataclasses import dataclass


def sections(text, size=900):
    start = 0
    while start < len(text):
        end = min(len(text), start + size)
        if end < len(text):
            for separator in ("\n", ". ", " "):
                boundary = text.rfind(separator, start + size // 2, end)
                if boundary >= 0:
                    end = boundary + len(separator)
                    break
        yield start, text[start:end]
        start = end


def terms(text):
    return set(re.findall(r"\w{3,}", text.casefold())) - {
        "the", "and", "with", "for", "from", "slide", "slides", "presentation", "that", "this", "include",
    }


def table_cells(line):
    line = line.strip()
    if line.startswith("|") and line.endswith("|"):
        return [cell.strip() for cell in line[1:-1].split("|")]
    return []


@dataclass
class MarkdownTable:
    start: int
    end: int
    header: str
    separator: str
    rows: list[str]
    before: str
    after: str
    heading: str


TABLE_NOTE = re.compile(
    r"^(?:\*{0,2})(?:(?:notes?|source|units?|currency)\b|(?:all\s+)?(?:figures|amounts|values|numbers|prices|data)\b|"
    r"as of\b|snapshot\b|\(?[A-Z]{3}\s+(?:millions?|billions?|thousands?))", re.I)
TABLE_FOOTNOTE = re.compile(r"^\*{0,2}(?:notes?|source)\s*:", re.I)


def markdown_tables(text):
    """Keep each table with its own heading and adjacent caption, never another table's rows."""
    lines = text.splitlines()
    headings = []
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if match := re.match(r"^(#{1,6})\s+(.+)", line):
            depth = len(match[1])
            headings = [(level, title) for level, title in headings if level < depth]
            headings.append((depth, line))
        header = table_cells(line)
        separator = table_cells(lines[i + 1]) if i + 1 < len(lines) else []
        if not (header and len(header) == len(separator) and
                all(re.fullmatch(r":?-{3,}:?", cell) for cell in separator)):
            i += 1
            continue
        end = i + 2
        while end < len(lines) and len(table_cells(lines[end])) == len(header):
            end += 1
        caption_start = i
        while caption_start and not lines[caption_start - 1].strip():
            caption_start -= 1
        caption_end = caption_start
        while caption_start and lines[caption_start - 1].strip() and not (
                table_cells(lines[caption_start - 1]) or lines[caption_start - 1].lstrip().startswith("#")):
            caption_start -= 1
        previous = caption_start - 1
        while previous >= 0 and not lines[previous].strip():
            previous -= 1
        if (caption_start < caption_end and TABLE_FOOTNOTE.match(lines[caption_start].strip()) and
                previous >= 0 and table_cells(lines[previous])):
            caption_start = caption_end  # This is the previous table's note, not this table's denomination.
        before = "\n".join([*(title for _, title in headings), *lines[caption_start:caption_end]])
        after_start = end
        while after_start < len(lines) and not lines[after_start].strip():
            after_start += 1
        after_end = after_start
        if after_start < len(lines) and TABLE_NOTE.match(lines[after_start].strip()):
            while after_end < len(lines) and lines[after_end].strip() and not (
                    table_cells(lines[after_end]) or lines[after_end].lstrip().startswith("#")):
                after_end += 1
            following = after_end
            while following < len(lines) and not lines[following].strip():
                following += 1
            if (following < len(lines) and table_cells(lines[following]) and
                    not TABLE_FOOTNOTE.match(lines[after_start].strip())):
                after_end = after_start  # An unmarked caption before another table belongs to that table.
        yield MarkdownTable(i, end, line, lines[i + 1].strip(),
                            [row.strip() for row in lines[i + 2:end]], before,
                            "\n".join(lines[after_start:after_end]),
                            headings[-1][1].lstrip("#").strip() if headings else "")
        i = end


MONTHS = ("january", "february", "march", "april", "may", "june", "july", "august", "september",
          "october", "november", "december")


def passage_terms(text):
    words = terms(text)
    years = set(re.findall(r"\b(?:19|20)\d{2}\b", text))
    months = set()
    for month, name in enumerate(MONTHS, 1):
        if re.search(rf"\b(?:{name}|{name[:3]}|{'sept' if month == 9 else name})\b", text, re.I):
            months.add(month)
    for year, month in re.findall(r"\b((?:19|20)\d{2})[-/](0?[1-9]|1[0-2])(?:\b|[-/])", text):
        months.add(int(month))
        words.add(f"period:{year}-{int(month):02}")
    for quarter in re.findall(r"\bQ([1-4])\b", text, re.I):
        months.update(range(3 * int(quarter) - 2, 3 * int(quarter) + 1))
    words.update(f"month:{month:02}" for month in months)
    if len(years) == 1:
        words.update(f"period:{next(iter(years))}-{month:02}" for month in months)
    return words


def text_passage(text, query, size):
    if not text:
        return ""
    words = passage_terms(query)
    chunks = sections(text, size)
    start, passage = max(chunks, key=lambda entry: len(words & passage_terms(entry[1])))
    end = start + len(passage)
    # sections() can split a long token. Keep only complete words and values.
    if start and not text[start - 1].isspace() and not text[start].isspace():
        passage = re.sub(r"^\S+", "", passage)
    if end < len(text) and not text[end].isspace() and not text[end - 1].isspace():
        passage = re.sub(r"\S+$", "", passage)
    return passage


def web_passage(text, query, size):
    """Select bounded, relevant passages without separating table rows from their units or scope."""
    if size <= 0:
        return ""
    if len(text) <= size:
        return text
    tables = list(markdown_tables(text))
    if not tables:
        return text_passage(text, query, size)
    words = passage_terms(query)
    contexts = [passage_terms("\n".join((table.before, table.header, table.after))) for table in tables]
    candidates = [(i, j, passage_terms(row)) for i, table in enumerate(tables) for j, row in enumerate(table.rows)]
    weights = {word: 1 / max(1, sum(word in tokens for _, _, tokens in candidates)) for word in words}
    # A shared date range identifies the table; only a row's own date identifies December's value.
    ranked = sorted(candidates, key=lambda entry: (
        -sum(weights[word] for tokens in (entry[2], contexts[entry[0]]) for word in words & tokens),
        entry[0], entry[1]))
    selected = {}

    def render():
        parts = []
        for i in sorted(selected):
            table = tables[i]
            rows = [table.rows[j] for j in sorted(selected[i])]
            parts.append("\n".join(part for part in (table.before, table.header, table.separator, *rows, table.after,
                         "[Selected source rows; other rows omitted.]" if len(rows) < len(table.rows) else "") if part))
        return "\n\n".join(parts)

    for i, j, _ in ranked:
        selected.setdefault(i, []).append(j)
        if len(render()) > size:
            selected[i].pop()
            if not selected[i]:
                del selected[i]
    passage = render()
    # A table elsewhere in a page must not hide a more relevant prose passage.
    lines = text.splitlines()
    table_lines = {i for table in tables for i in range(table.start, table.end)}
    prose = text_passage("\n".join(line for i, line in enumerate(lines) if i not in table_lines), query, size)
    if len(words & passage_terms(prose)) > len(words & passage_terms(passage)):
        return prose
    return passage or prose


def source_packet(source, query, *, inventory=False):
    text = source.get("excerpt", "")
    if len(text) <= 3000 or (source.get("url") and not source.get("asset_id")):
        return source
    chunks = list(sections(text))
    words = terms(query)
    # Prefer discriminating terms, and include the conclusion when no section is a better match.
    vocabulary = [terms(chunk) for _, chunk in chunks]
    weights = {word: 1 / max(1, sum(word in section for section in vocabulary)) for word in words}
    ranked = sorted(range(len(chunks)), key=lambda i: (
        -sum(weights[w] for w in vocabulary[i] & words), i not in (0, len(chunks)-1), i))
    selected = sorted(ranked[:3])
    packet = {**source, "excerpt": "\n".join(chunks[i][1].strip() for i in selected),
              "section_offsets": [chunks[i][0] for i in selected]}
    if inventory:
        packet["sections"] = [{"offset": offset, "starts_with": chunk.strip().split("\n", 1)[0][:90]}
                              for offset, chunk in chunks]
    return packet
