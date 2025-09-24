from sqlmodel import select
from app import database
from app.auth.models import HouseMembership, RoomAccess, User

username = "hub-admin"

with database.SessionLocal() as session:
    user = session.exec(select(User).where(User.username == username)).first()
    if not user:
        raise SystemExit("user not found")

    memberships = session.exec(
        select(HouseMembership).where(HouseMembership.user_id == user.id)
    ).all()

    for membership in memberships:
        session.exec(
            select(RoomAccess)
            .where(RoomAccess.membership_id == membership.id)
        )
        session.query(RoomAccess).filter_by(membership_id=membership.id).delete()
        session.delete(membership)

    session.delete(user)
    session.commit()
