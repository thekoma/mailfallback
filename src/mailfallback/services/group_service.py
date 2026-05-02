# src/mailfallback/services/group_service.py
from sqlalchemy.orm import Session

from mailfallback.models import Account, Group, User, UserRole


def create_group(db: Session, name: str, owner_id: str, sso_sync: bool = False) -> Group:
    group = Group(name=name, owner_id=owner_id, sso_sync=sso_sync)
    db.add(group)
    db.commit()
    db.refresh(group)
    return group


_UPDATABLE_GROUP_FIELDS = {"name", "sso_sync"}


def update_group(db: Session, group_id: str, **kwargs) -> Group | None:
    group = db.query(Group).filter(Group.id == group_id).first()
    if not group:
        return None
    for key, value in kwargs.items():
        if key in _UPDATABLE_GROUP_FIELDS:
            setattr(group, key, value)
    db.commit()
    db.refresh(group)
    return group


def delete_group(db: Session, group_id: str) -> bool:
    group = db.query(Group).filter(Group.id == group_id).first()
    if not group:
        return False
    db.delete(group)
    db.commit()
    return True


def add_member(db: Session, group_id: str, user_id: str) -> None:
    group = db.query(Group).filter(Group.id == group_id).first()
    user = db.query(User).filter(User.id == user_id).first()
    if group and user and user not in group.members:
        group.members.append(user)
        db.commit()


def remove_member(db: Session, group_id: str, user_id: str) -> None:
    group = db.query(Group).filter(Group.id == group_id).first()
    user = db.query(User).filter(User.id == user_id).first()
    if group and user and user in group.members:
        group.members.remove(user)
        db.commit()


def set_group_accounts(db: Session, group_id: str, account_ids: list[str]) -> None:
    group = db.query(Group).filter(Group.id == group_id).first()
    if not group:
        return
    accounts = db.query(Account).filter(Account.id.in_(account_ids)).all()
    group.accounts = accounts
    db.commit()


def get_user_groups(db: Session, user: User) -> list[Group]:
    return user.groups


def can_manage_group(user: User, group: Group) -> bool:
    if user.role == UserRole.admin:
        return True
    return user.id == group.owner_id


def sync_sso_groups(db: Session, user: User, sso_group_names: list[str]) -> None:
    sso_groups = db.query(Group).filter(Group.sso_sync.is_(True)).all()
    for group in sso_groups:
        is_member = user in group.members
        should_be_member = group.name in sso_group_names
        if should_be_member and not is_member:
            group.members.append(user)
        elif not should_be_member and is_member:
            group.members.remove(user)
    db.commit()
