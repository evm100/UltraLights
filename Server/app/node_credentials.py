"""Database-backed node credential helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, List, Optional, Tuple

from sqlmodel import Session, select

from . import registry
from .auth.models import NodeCredential


@dataclass
class NodeCredentialWithToken:
    """Return value that optionally includes a freshly issued token."""

    credential: NodeCredential
    plaintext_token: Optional[str]


def _now() -> datetime:
    return datetime.utcnow()


def _first_result(result: Any) -> Any:
    """Return the first item from a SQLModel result or stub."""

    if hasattr(result, "first"):
        return result.first()
    if hasattr(result, "one_or_none"):
        try:
            return result.one_or_none()
        except Exception:  # pragma: no cover - defensive fallback
            pass
    if hasattr(result, "all"):
        rows = result.all()
        return rows[0] if rows else None
    try:
        iterator = iter(result)
    except TypeError:  # pragma: no cover - defensive
        return None
    return next(iterator, None)


def _get_by_node_id(session: Session, node_id: str) -> Optional[NodeCredential]:
    result = session.exec(
        select(NodeCredential).where(NodeCredential.node_id == node_id)
    )
    return _first_result(result)


def get_by_node_id(session: Session, node_id: str) -> Optional[NodeCredential]:
    return _get_by_node_id(session, node_id)


def get_by_download_id(session: Session, download_id: str) -> Optional[NodeCredential]:
    result = session.exec(
        select(NodeCredential).where(NodeCredential.download_id == download_id)
    )
    return _first_result(result)


def get_by_token_hash(session: Session, token_hash: str) -> Optional[NodeCredential]:
    result = session.exec(
        select(NodeCredential).where(NodeCredential.token_hash == token_hash)
    )
    return _first_result(result)


def any_tokens(session: Session) -> bool:
    result = session.exec(select(NodeCredential.id))
    return _first_result(result) is not None


def ensure_for_node(
    session: Session,
    *,
    node_id: str,
    house_slug: str,
    room_id: str,
    display_name: str,
    download_id: Optional[str] = None,
    token_hash: Optional[str] = None,
    rotate_token: bool = False,
) -> NodeCredentialWithToken:
    """Ensure a credential row exists for ``node_id`` and return it."""

    credential = _get_by_node_id(session, node_id)
    plaintext: Optional[str] = None

    if credential:
        changed = False
        if credential.house_slug != house_slug:
            credential.house_slug = house_slug
            changed = True
        if credential.room_id != room_id:
            credential.room_id = room_id
            changed = True
        if credential.display_name != display_name:
            credential.display_name = display_name
            changed = True
        if download_id and credential.download_id != download_id:
            credential.download_id = download_id
            changed = True

        if rotate_token:
            plaintext = registry.generate_node_token()
            credential.token_hash = registry.hash_node_token(plaintext)
            credential.token_issued_at = _now()
            changed = True
        elif token_hash and credential.token_hash != token_hash:
            credential.token_hash = token_hash
            credential.token_issued_at = _now()
            changed = True

        if changed:
            session.add(credential)
            session.commit()
            session.refresh(credential)

        return NodeCredentialWithToken(credential=credential, plaintext_token=plaintext)

    if download_id is None:
        download_id = registry.generate_download_id()

    if rotate_token:
        plaintext = registry.generate_node_token()
        token_hash = registry.hash_node_token(plaintext)
    elif token_hash is None:
        plaintext = registry.generate_node_token()
        token_hash = registry.hash_node_token(plaintext)
    else:
        plaintext = None

    credential = NodeCredential(
        node_id=node_id,
        house_slug=house_slug,
        room_id=room_id,
        display_name=display_name,
        download_id=download_id,
        token_hash=token_hash,
        created_at=_now(),
        token_issued_at=_now(),
    )
    session.add(credential)
    session.commit()
    session.refresh(credential)

    return NodeCredentialWithToken(credential=credential, plaintext_token=plaintext)


def rotate_token(
    session: Session, node_id: str, *, token: Optional[str] = None
) -> Tuple[NodeCredential, str]:
    credential = _get_by_node_id(session, node_id)
    if credential is None:
        raise KeyError("node credentials not found")

    plaintext = token or registry.generate_node_token()
    credential.token_hash = registry.hash_node_token(plaintext)
    credential.token_issued_at = _now()
    session.add(credential)
    session.commit()
    session.refresh(credential)
    return credential, plaintext


def update_download_id(
    session: Session, node_id: str, download_id: Optional[str] = None
) -> NodeCredential:
    credential = _get_by_node_id(session, node_id)
    if credential is None:
        raise KeyError("node credentials not found")

    new_download = download_id or registry.generate_download_id()
    credential.download_id = new_download
    session.add(credential)
    session.commit()
    session.refresh(credential)
    return credential


def mark_provisioned(
    session: Session, node_id: str, *, timestamp: Optional[datetime] = None
) -> NodeCredential:
    credential = _get_by_node_id(session, node_id)
    if credential is None:
        raise KeyError("node credentials not found")

    credential.provisioned_at = timestamp or _now()
    session.add(credential)
    session.commit()
    session.refresh(credential)
    return credential


def clear_provisioned(session: Session, node_id: str) -> NodeCredential:
    credential = _get_by_node_id(session, node_id)
    if credential is None:
        raise KeyError("node credentials not found")

    credential.provisioned_at = None
    session.add(credential)
    session.commit()
    session.refresh(credential)
    return credential


def delete_credentials(session: Session, node_id: str) -> None:
    credential = _get_by_node_id(session, node_id)
    if credential is None:
        return
    session.delete(credential)
    session.commit()


def list_unprovisioned(session: Session) -> List[NodeCredential]:
    return session.exec(
        select(NodeCredential).where(NodeCredential.provisioned_at.is_(None))
    ).all()


def sync_registry_nodes(session: Session) -> None:
    """Ensure every registry node has a credential entry and synced download id."""

    registry.ensure_house_external_ids(persist=False)

    changed = False
    for house, room, node in registry.iter_nodes():
        node_id = str(node.get("id") or "").strip()
        if not node_id:
            continue

        house_slug = registry.get_house_slug(house)
        room_id = str(room.get("id") or "").strip()
        display_name = str(node.get("name") or node_id)

        raw_download = node.get(registry.NODE_DOWNLOAD_ID_KEY)
        download_id = str(raw_download).strip() if isinstance(raw_download, str) else None
        if download_id and any(
            ch not in registry.DOWNLOAD_ID_ALPHABET for ch in download_id
        ):
            download_id = None

        raw_token_hash = node.get(registry.NODE_TOKEN_HASH_KEY)
        token_hash = (
            str(raw_token_hash).strip()
            if isinstance(raw_token_hash, str) and raw_token_hash
            else None
        )

        existing = get_by_node_id(session, node_id)
        ensured = ensure_for_node(
            session,
            node_id=node_id,
            house_slug=house_slug,
            room_id=room_id,
            display_name=display_name,
            download_id=download_id if existing is None or not existing.download_id else None,
            token_hash=token_hash if existing is None or not existing.token_hash else None,
        )

        credential = ensured.credential
        if credential.download_id != download_id:
            node[registry.NODE_DOWNLOAD_ID_KEY] = credential.download_id
            changed = True

        if registry.NODE_TOKEN_HASH_KEY in node:
            node.pop(registry.NODE_TOKEN_HASH_KEY, None)
            changed = True

    if changed:
        registry.save_registry()


__all__ = [
    "NodeCredentialWithToken",
    "any_tokens",
    "clear_provisioned",
    "delete_credentials",
    "ensure_for_node",
    "get_by_node_id",
    "get_by_download_id",
    "get_by_token_hash",
    "list_unprovisioned",
    "mark_provisioned",
    "rotate_token",
    "sync_registry_nodes",
    "update_download_id",
]
