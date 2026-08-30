class KnowledgeTagValidationError(ValueError):
    """Raised when a knowledge tag list cannot be stored safely."""


MAX_KNOWLEDGE_TAGS = 20
MAX_KNOWLEDGE_TAG_LENGTH = 40


def normalize_knowledge_tags(tags: list[str] | None) -> list[str]:
    """Trim tags and remove duplicates without changing the first display label."""
    if tags is None:
        return []

    normalized: list[str] = []
    seen: set[str] = set()
    for raw_tag in tags:
        tag = raw_tag.strip()
        if not tag:
            continue
        if len(tag) > MAX_KNOWLEDGE_TAG_LENGTH:
            raise KnowledgeTagValidationError("knowledge_tag_too_long")
        key = tag.casefold()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(tag)

    if len(normalized) > MAX_KNOWLEDGE_TAGS:
        raise KnowledgeTagValidationError("knowledge_tag_limit_exceeded")
    return normalized
