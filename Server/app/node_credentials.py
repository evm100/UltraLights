"""Database-backed node credential helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import logging
from typing import Any, Dict, Iterable, List, Optional, Tuple

from sqlmodel import Session, select

from . import registry
from .auth.models import (
    HouseMembership,
    NodeCredential,
    NodeRegistration,
    User,
)
from .auth.security import hash_password, verify_password


@dataclass
class NodeCredentialWithToken:
    """Return value that optionally includes a freshly issued token."""

    credential: NodeCredential
    plaintext_token: Optional[str]


@dataclass
class NodeRegistrationWithToken:
    """Batch generation return type including the plaintext token."""

    registration: NodeRegistration
    plaintext_token: str


def _now() -> datetime:
    """Return a timezone-aware UTC timestamp."""

    return datetime.now(timezone.utc)


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


def _get_registration_by_node_id(
    session: Session, node_id: str
) -> Optional[NodeRegistration]:
    result = session.exec(
        select(NodeRegistration).where(NodeRegistration.node_id == node_id)
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


def get_registration_by_node_id(
    session: Session, node_id: str
) -> Optional[NodeRegistration]:
    return _get_registration_by_node_id(session, node_id)


def get_registration_by_download_id(
    session: Session, download_id: str
) -> Optional[NodeRegistration]:
    result = session.exec(
        select(NodeRegistration).where(NodeRegistration.download_id == download_id)
    )
    return _first_result(result)


def any_tokens(session: Session) -> bool:
    """Return True if any node credentials or registrations exist."""

    if _first_result(session.exec(select(NodeCredential.id))):
        return True
    return _first_result(session.exec(select(NodeRegistration.id))) is not None


def create_batch(
    session: Session,
    count: int,
    *,
    metadata: Optional[Iterable[Dict[str, Any]]] = None,
) -> List[NodeRegistrationWithToken]:
    """Generate ``count`` opaque registrations and persist them."""

    if count <= 0:
        raise ValueError("count must be positive")

    registrations: List[NodeRegistrationWithToken] = []

    def _collect_strings(statement) -> set[str]:
        values: set[str] = set()
        for row in session.exec(statement).all():
            candidate = row[0] if isinstance(row, tuple) else row
            if isinstance(candidate, str):
                values.add(candidate)
        return values

    existing_node_ids = _collect_strings(select(NodeRegistration.node_id))
    existing_node_ids.update(_collect_strings(select(NodeCredential.node_id)))
    existing_download_ids = _collect_strings(select(NodeRegistration.download_id))
    existing_download_ids.update(_collect_strings(select(NodeCredential.download_id)))

    metadata_list: List[Dict[str, Any]] = []
    if metadata is not None:
        for entry in metadata:
            metadata_list.append(dict(entry))

    for index in range(count):
        node_id = registry.generate_node_id()
        while node_id in existing_node_ids:
            node_id = registry.generate_node_id()
        existing_node_ids.add(node_id)

        download_id = registry.generate_download_id()
        while download_id in existing_download_ids:
            download_id = registry.generate_download_id()
        existing_download_ids.add(download_id)

        plaintext_token = registry.generate_node_token()
        token_hash = registry.hash_node_token(plaintext_token)

        metadata_entry: Dict[str, Any] = (
            dict(metadata_list[index]) if index < len(metadata_list) else {}
        )

        registration = NodeRegistration(
            node_id=node_id,
            download_id=download_id,
            token_hash=token_hash,
            provisioning_token=plaintext_token,
            hardware_metadata=metadata_entry,
        )
        session.add(registration)
        registrations.append(
            NodeRegistrationWithToken(
                registration=registration, plaintext_token=plaintext_token
            )
        )

    session.commit()
    for entry in registrations:
        session.refresh(entry.registration)

    return registrations


def list_available_registrations(session: Session) -> List[NodeRegistration]:
    """Return registrations that have not been assigned to a house/user."""

    result = session.exec(
        select(NodeRegistration).where(NodeRegistration.assigned_at.is_(None))
    )
    return result.all()


def list_assigned_registrations(session: Session) -> List[NodeRegistration]:
    """Return registrations that have been associated with a house or user."""

    result = session.exec(
        select(NodeRegistration).where(NodeRegistration.assigned_at.is_not(None))
    )
    return result.all()


def claim_registration(
    session: Session,
    node_id: str,
    *,
    house_slug: Optional[str] = None,
    room_id: Optional[str] = None,
    display_name: Optional[str] = None,
    assigned_user_id: Optional[int] = None,
    assigned_house_id: Optional[int] = None,
    hardware_metadata: Optional[Dict[str, Any]] = None,
) -> NodeRegistration:
    """Mark a pre-generated registration as claimed for later assignment."""

    registration = _get_registration_by_node_id(session, node_id)
    if registration is None:
        raise KeyError("node registration not found")

    changed = False
    now = _now()

    if registration.assigned_at is None:
        registration.assigned_at = now
        changed = True

    if house_slug is not None and registration.house_slug != house_slug:
        registration.house_slug = house_slug
        changed = True
    if room_id is not None and registration.room_id != room_id:
        registration.room_id = room_id
        changed = True
    if display_name is not None and registration.display_name != display_name:
        registration.display_name = display_name
        changed = True
    if assigned_user_id is not None and registration.assigned_user_id != assigned_user_id:
        registration.assigned_user_id = assigned_user_id
        changed = True
    if assigned_house_id is not None and registration.assigned_house_id != assigned_house_id:
        registration.assigned_house_id = assigned_house_id
        changed = True
    if hardware_metadata:
        merged = dict(registration.hardware_metadata)
        merged.update(hardware_metadata)
        if merged != registration.hardware_metadata:
            registration.hardware_metadata = merged
            changed = True

    if changed:
        session.add(registration)
        session.commit()
        session.refresh(registration)

    return registration


def _sync_registration_assignment(
    registration: NodeRegistration,
    *,
    house_slug: str,
    room_id: str,
    display_name: str,
    assigned_house_id: Optional[int],
    assigned_user_id: Optional[int],
    hardware_metadata: Optional[Dict[str, Any]],
) -> Tuple[NodeRegistration, bool]:
    changed = False
    now = _now()

    if registration.assigned_at is None:
        registration.assigned_at = now
        changed = True

    if registration.house_slug != house_slug:
        registration.house_slug = house_slug
        changed = True
    if registration.room_id != room_id:
        registration.room_id = room_id
        changed = True
    if registration.display_name != display_name:
        registration.display_name = display_name
        changed = True
    if assigned_house_id is not None and registration.assigned_house_id != assigned_house_id:
        registration.assigned_house_id = assigned_house_id
        changed = True
    if assigned_user_id is not None and registration.assigned_user_id != assigned_user_id:
        registration.assigned_user_id = assigned_user_id
        changed = True
    if hardware_metadata:
        merged = dict(registration.hardware_metadata)
        merged.update(hardware_metadata)
        if merged != registration.hardware_metadata:
            registration.hardware_metadata = merged
            changed = True

    return registration, changed


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
    assigned_house_id: Optional[int] = None,
    assigned_user_id: Optional[int] = None,
    hardware_metadata: Optional[Dict[str, Any]] = None,
) -> NodeCredentialWithToken:
    """Ensure a credential row exists for ``node_id`` and return it."""

    plaintext: Optional[str] = None
    registration = _get_registration_by_node_id(session, node_id)
    registration_changed = False

    if registration is None:
        if download_id is None:
            download_id = registry.generate_download_id()
        if rotate_token or token_hash is None:
            plaintext = registry.generate_node_token()
            token_hash = registry.hash_node_token(plaintext)
        else:
            plaintext = None
        registration = NodeRegistration(
            node_id=node_id,
            download_id=download_id,
            token_hash=token_hash,
            provisioning_token=plaintext,
            assigned_at=_now(),
            house_slug=house_slug,
            room_id=room_id,
            display_name=display_name,
            assigned_house_id=assigned_house_id,
            assigned_user_id=assigned_user_id,
            hardware_metadata=hardware_metadata or {},
        )
        registration_changed = True
    else:
        registration, updated = _sync_registration_assignment(
            registration,
            house_slug=house_slug,
            room_id=room_id,
            display_name=display_name,
            assigned_house_id=assigned_house_id,
            assigned_user_id=assigned_user_id,
            hardware_metadata=hardware_metadata,
        )
        registration_changed |= updated

        if download_id and registration.download_id != download_id:
            registration.download_id = download_id
            registration_changed = True

        if rotate_token:
            plaintext = registry.generate_node_token()
            registration.token_hash = registry.hash_node_token(plaintext)
            registration.token_issued_at = _now()
            registration.provisioning_token = plaintext
            registration_changed = True
        elif token_hash and registration.token_hash != token_hash:
            registration.token_hash = token_hash
            registration.token_issued_at = _now()
            registration_changed = True

    credential = _get_by_node_id(session, node_id)
    credential_changed = False

    if credential:
        if credential.house_slug != house_slug:
            credential.house_slug = house_slug
            credential_changed = True
        if credential.room_id != room_id:
            credential.room_id = room_id
            credential_changed = True
        if credential.display_name != display_name:
            credential.display_name = display_name
            credential_changed = True
        if credential.download_id != registration.download_id:
            credential.download_id = registration.download_id
            credential_changed = True

        if rotate_token:
            plaintext = plaintext or registry.generate_node_token()
            registration.token_hash = registry.hash_node_token(plaintext)
            registration.token_issued_at = _now()
            registration.provisioning_token = plaintext
            credential.token_hash = registration.token_hash
            credential.token_issued_at = registration.token_issued_at
            credential_changed = True
            registration_changed = True
        elif credential.token_hash != registration.token_hash:
            credential.token_hash = registration.token_hash
            credential.token_issued_at = registration.token_issued_at
            credential_changed = True
    else:
        if plaintext:
            token_hash = registry.hash_node_token(plaintext)
            if registration.token_hash != token_hash:
                registration.token_hash = token_hash
                registration.token_issued_at = _now()
                registration_changed = True
            if registration.provisioning_token != plaintext:
                registration.provisioning_token = plaintext
                registration_changed = True
        credential = NodeCredential(
            node_id=node_id,
            house_slug=house_slug,
            room_id=room_id,
            display_name=display_name,
            download_id=registration.download_id,
            token_hash=registration.token_hash,
            created_at=_now(),
            token_issued_at=registration.token_issued_at,
        )
        credential_changed = True

    if registration_changed:
        session.add(registration)
    if credential_changed:
        session.add(credential)
    if registration_changed or credential_changed:
        session.commit()
        if registration_changed:
            session.refresh(registration)
        if credential_changed:
            session.refresh(credential)

    return NodeCredentialWithToken(credential=credential, plaintext_token=plaintext)


def rotate_token(
    session: Session, node_id: str, *, token: Optional[str] = None
) -> Tuple[NodeCredential, str]:
    credential = _get_by_node_id(session, node_id)
    registration = _get_registration_by_node_id(session, node_id)
    if credential is None and registration is None:
        raise KeyError("node credentials not found")

    plaintext = token or registry.generate_node_token()
    token_hash = registry.hash_node_token(plaintext)
    issued_at = _now()

    if credential is not None:
        credential.token_hash = token_hash
        credential.token_issued_at = issued_at
        session.add(credential)

    if registration is not None:
        registration.token_hash = token_hash
        registration.token_issued_at = issued_at
        registration.provisioning_token = plaintext
        session.add(registration)

    session.commit()

    if credential is not None:
        session.refresh(credential)
        return credential, plaintext

    session.refresh(registration)
    # Legacy callers expect a credential, so fabricate a placeholder when
    # only a registration exists.
    legacy = NodeCredential(
        node_id=registration.node_id,
        house_slug=registration.house_slug or "",
        room_id=registration.room_id or "",
        display_name=registration.display_name or registration.node_id,
        download_id=registration.download_id,
        token_hash=registration.token_hash,
        created_at=registration.created_at,
        token_issued_at=registration.token_issued_at,
    )
    return legacy, plaintext


def record_account_credentials(
    session: Session, node_id: str, username: str, password: str
) -> Optional[NodeRegistration]:
    """Persist account credentials observed from firmware."""

    username = (username or "").strip()
    password = password or ""
    if not username or not password:
        raise ValueError("username and password are required")

    registration = _get_registration_by_node_id(session, node_id)
    if registration is None:
        raise KeyError("node registration not found")

    user = _first_result(session.exec(select(User).where(User.username == username)))
    if user is None:
        logging.warning(
            "Received credentials for unknown user '%s' on node '%s'",
            username,
            node_id,
        )
        return None

    if not verify_password(password, user.hashed_password):
        logging.warning(
            "Credential verification failed for user '%s' on node '%s'",
            username,
            node_id,
        )
        return None

    hashed = hash_password(password)
    now = _now()
    changed = False

    if registration.account_username != username:
        registration.account_username = username
        changed = True
    if registration.account_password_hash != hashed:
        registration.account_password_hash = hashed
        changed = True
    registration.account_credentials_received_at = now
    changed = True

    if registration.assigned_user_id != user.id:
        registration.assigned_user_id = user.id
        changed = True

    membership = _first_result(
        session.exec(
            select(HouseMembership).where(HouseMembership.user_id == user.id)
        )
    )
    if membership and registration.assigned_house_id != membership.house_id:
        registration.assigned_house_id = membership.house_id
        changed = True

    if changed:
        session.add(registration)
        session.commit()
        session.refresh(registration)

    logging.info(
        "Associated node '%s' with user '%s'%s",
        node_id,
        username,
        "" if not membership else f" in house {membership.house_id}",
    )

    return registration


def update_download_id(
    session: Session, node_id: str, download_id: Optional[str] = None
) -> NodeCredential:
    credential = _get_by_node_id(session, node_id)
    registration = _get_registration_by_node_id(session, node_id)
    if credential is None and registration is None:
        raise KeyError("node credentials not found")

    new_download = download_id or registry.generate_download_id()

    if credential is not None:
        credential.download_id = new_download
        session.add(credential)

    if registration is not None:
        registration.download_id = new_download
        session.add(registration)

    session.commit()

    if credential is not None:
        session.refresh(credential)
        if registration is not None:
            session.refresh(registration)
        return credential

    session.refresh(registration)
    legacy = NodeCredential(
        node_id=registration.node_id,
        house_slug=registration.house_slug or "",
        room_id=registration.room_id or "",
        display_name=registration.display_name or registration.node_id,
        download_id=registration.download_id,
        token_hash=registration.token_hash,
        created_at=registration.created_at,
        token_issued_at=registration.token_issued_at,
    )
    return legacy


def mark_provisioned(
    session: Session, node_id: str, *, timestamp: Optional[datetime] = None
) -> NodeCredential:
    credential = _get_by_node_id(session, node_id)
    registration = _get_registration_by_node_id(session, node_id)
    if credential is None and registration is None:
        raise KeyError("node credentials not found")

    stamp = timestamp or _now()

    if credential is not None:
        credential.provisioned_at = stamp
        session.add(credential)
    if registration is not None:
        registration.provisioned_at = stamp
        session.add(registration)

    session.commit()

    if credential is not None:
        session.refresh(credential)
        if registration is not None:
            session.refresh(registration)
        return credential

    session.refresh(registration)
    legacy = NodeCredential(
        node_id=registration.node_id,
        house_slug=registration.house_slug or "",
        room_id=registration.room_id or "",
        display_name=registration.display_name or registration.node_id,
        download_id=registration.download_id,
        token_hash=registration.token_hash,
        created_at=registration.created_at,
        token_issued_at=registration.token_issued_at,
        provisioned_at=registration.provisioned_at,
    )
    return legacy


def clear_provisioned(session: Session, node_id: str) -> NodeCredential:
    credential = _get_by_node_id(session, node_id)
    registration = _get_registration_by_node_id(session, node_id)
    if credential is None and registration is None:
        raise KeyError("node credentials not found")

    if credential is not None:
        credential.provisioned_at = None
        session.add(credential)
    if registration is not None:
        registration.provisioned_at = None
        session.add(registration)

    session.commit()

    if credential is not None:
        session.refresh(credential)
        return credential

    session.refresh(registration)
    legacy = NodeCredential(
        node_id=registration.node_id,
        house_slug=registration.house_slug or "",
        room_id=registration.room_id or "",
        display_name=registration.display_name or registration.node_id,
        download_id=registration.download_id,
        token_hash=registration.token_hash,
        created_at=registration.created_at,
        token_issued_at=registration.token_issued_at,
    )
    return legacy


def delete_credentials(session: Session, node_id: str) -> None:
    credential = _get_by_node_id(session, node_id)
    registration = _get_registration_by_node_id(session, node_id)

    if credential is None and registration is None:
        return

    if credential is not None:
        session.delete(credential)
    if registration is not None:
        session.delete(registration)

    session.commit()


def list_unprovisioned(session: Session) -> List[NodeCredential]:
    return session.exec(
        select(NodeCredential).where(NodeCredential.provisioned_at.is_(None))
    ).all()


def list_unprovisioned_registrations(session: Session) -> List[NodeRegistration]:
    return session.exec(
        select(NodeRegistration).where(NodeRegistration.provisioned_at.is_(None))
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

        existing_registration = _get_registration_by_node_id(session, node_id)
        existing_credential = _get_by_node_id(session, node_id)
        existing_download = None
        existing_token = None
        if existing_registration is not None:
            existing_download = existing_registration.download_id
            existing_token = existing_registration.token_hash
        elif existing_credential is not None:
            existing_download = existing_credential.download_id
            existing_token = existing_credential.token_hash

        ensured = ensure_for_node(
            session,
            node_id=node_id,
            house_slug=house_slug,
            room_id=room_id,
            display_name=display_name,
            download_id=download_id if not existing_download else None,
            token_hash=token_hash if not existing_token else None,
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
    "NodeRegistrationWithToken",
    "any_tokens",
    "claim_registration",
    "clear_provisioned",
    "create_batch",
    "delete_credentials",
    "ensure_for_node",
    "get_by_node_id",
    "get_by_download_id",
    "get_by_token_hash",
    "get_registration_by_download_id",
    "get_registration_by_node_id",
    "list_assigned_registrations",
    "list_available_registrations",
    "list_unprovisioned",
    "list_unprovisioned_registrations",
    "mark_provisioned",
    "record_account_credentials",
    "rotate_token",
    "sync_registry_nodes",
    "update_download_id",
]
