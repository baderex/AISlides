"""Select citable passages from the complete retained text of an upload."""
import re


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


def web_passage(text, query, size):
    """Keep a bounded passage, ending before any incomplete table row or number."""
    if len(text) <= size:
        return text
    if re.search(r"(?m)^\s*\|", text):
        prefix = text[:size + 1]
        if "\n" in prefix:
            return prefix.rsplit("\n", 1)[0]
    words = terms(query)
    chunks = sections(text, size)
    start, passage = max(chunks, key=lambda entry: len(words & terms(entry[1])))
    end = start + len(passage)
    # sections() can split a long token. Keep only complete words and values.
    if start and not text[start - 1].isspace() and not text[start].isspace():
        passage = re.sub(r"^\S+", "", passage)
    if end < len(text) and not text[end].isspace() and not text[end - 1].isspace():
        passage = re.sub(r"\S+$", "", passage)
    return passage


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
