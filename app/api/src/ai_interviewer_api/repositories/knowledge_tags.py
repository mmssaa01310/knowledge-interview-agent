from __future__ import annotations

from hashlib import sha256

from ai_interviewer_api.models.base import utc_now
from ai_interviewer_api.models.domain import KnowledgeTag
from ai_interviewer_api.repositories.store import store
from ai_interviewer_api.services.knowledge_tags import (
    normalize_knowledge_tag,
    normalize_knowledge_tags,
)


def _tag_entity_id(tenant_id: str, tag: str) -> str:
    digest = sha256(f"{tenant_id}\0{tag.casefold()}".encode("utf-8")).hexdigest()
    return f"knowledge-tag-{digest}"


def list_knowledge_tags(tenant_id: str) -> list[dict]:
    unique_tags: dict[str, dict] = {}
    for row in store.list("knowledge_tags", tenant_id):
        raw_name = row.get("name")
        name = raw_name.strip() if isinstance(raw_name, str) else ""
        if name:
            unique_tags.setdefault(name.casefold(), row)
    return sorted(unique_tags.values(), key=lambda row: str(row.get("name", "")).casefold())


def register_knowledge_tags(tenant_id: str, user_id: str, tags: list[str]) -> list[dict]:
    """Create missing tenant tags without removing tags no longer in use."""
    existing = {
        row["name"].strip().casefold()
        for row in store.list("knowledge_tags", tenant_id)
        if isinstance(row.get("name"), str) and row["name"].strip()
    }
    for raw_tag in tags:
        tag = normalize_knowledge_tag(raw_tag)
        if not tag or tag.casefold() in existing:
            continue
        now = utc_now()
        item = KnowledgeTag(
            id=_tag_entity_id(tenant_id, tag),
            tenantId=tenant_id,
            createdByUserId=user_id,
            updatedByUserId=user_id,
            createdAt=now,
            updatedAt=now,
            name=tag,
        )
        store.upsert("knowledge_tags", item.model_dump())
        existing.add(tag.casefold())
    return list_knowledge_tags(tenant_id)


def sync_knowledge_tags_from_knowledges(tenant_id: str, user_id: str) -> list[dict]:
    tags = [
        tag
        for knowledge in store.list("knowledges", tenant_id)
        for tag in knowledge.get("tags", [])
        if isinstance(tag, str)
    ]
    return register_knowledge_tags(tenant_id, user_id, tags)


def rename_knowledge_tag(
    tenant_id: str,
    user_id: str,
    tag_id: str,
    new_name: str,
) -> dict | None:
    tag = store.get("knowledge_tags", tag_id)
    if not tag or tag.get("tenantId") != tenant_id:
        return None

    normalized_name = normalize_knowledge_tag(new_name)
    if not normalized_name:
        raise ValueError("knowledge_tag_required")
    old_name = str(tag.get("name", "")).strip()
    old_key = old_name.casefold()
    new_key = normalized_name.casefold()
    duplicate = next(
        (
            row
            for row in store.list("knowledge_tags", tenant_id)
            if row.get("id") != tag_id
            and isinstance(row.get("name"), str)
            and row["name"].strip().casefold() == new_key
        ),
        None,
    )
    if duplicate:
        raise ValueError("knowledge_tag_already_exists")

    tag["name"] = normalized_name
    tag["updatedByUserId"] = user_id
    tag["updatedAt"] = utc_now()
    store.upsert("knowledge_tags", tag)

    if old_key and old_key != new_key:
        for knowledge in store.list("knowledges", tenant_id):
            current_tags = knowledge.get("tags", [])
            if not isinstance(current_tags, list):
                continue
            next_tags = [
                normalized_name if isinstance(value, str) and value.strip().casefold() == old_key else value
                for value in current_tags
            ]
            if next_tags == current_tags:
                continue
            knowledge["tags"] = normalize_knowledge_tags(next_tags)
            knowledge["updatedByUserId"] = user_id
            knowledge["updatedAt"] = utc_now()
            store.upsert("knowledges", knowledge)
    return tag


def delete_knowledge_tag(tenant_id: str, user_id: str, tag_id: str) -> dict | None:
    tag = store.get("knowledge_tags", tag_id)
    if not tag or tag.get("tenantId") != tenant_id:
        return None
    tag_name = str(tag.get("name", "")).strip().casefold()

    for knowledge in store.list("knowledges", tenant_id):
        current_tags = knowledge.get("tags", [])
        if not isinstance(current_tags, list):
            continue
        next_tags = [
            value
            for value in current_tags
            if not (isinstance(value, str) and value.strip().casefold() == tag_name)
        ]
        if next_tags == current_tags:
            continue
        knowledge["tags"] = normalize_knowledge_tags(next_tags)
        knowledge["updatedByUserId"] = user_id
        knowledge["updatedAt"] = utc_now()
        store.upsert("knowledges", knowledge)

    store.delete("knowledge_tags", tag_id)
    return tag
