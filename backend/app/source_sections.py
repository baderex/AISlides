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


def source_packet(source, query, *, inventory=False):
    text = source.get("excerpt", "")
    if not source.get("asset_id") or len(text) <= 3000:
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
