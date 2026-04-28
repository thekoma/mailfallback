# src/mailfallback/services/user_service.py
from sqlalchemy.orm import Session

from mailfallback.models import User, UserRole
from mailfallback.security import hash_password, verify_password


def create_user(db: Session, username: str, password: str, role: UserRole = UserRole.user) -> User:
    user = User(username=username, password_hash=hash_password(password), role=role)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def authenticate_user(db: Session, username: str, password: str) -> User | None:
    user = db.query(User).filter(User.username == username).first()
    if not user or not user.password_hash:
        return None
    if not user.enabled:
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user


def get_user_by_id(db: Session, user_id: str) -> User | None:
    return db.query(User).filter(User.id == user_id).first()


def list_users(db: Session) -> list[User]:
    return db.query(User).all()


def change_password(db: Session, user_id: str, new_password: str) -> bool:
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        return False
    user.password_hash = hash_password(new_password)
    db.commit()
    return True


def update_user(db: Session, user_id: str, **kwargs) -> User | None:
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        return None
    for key, value in kwargs.items():
        setattr(user, key, value)
    db.commit()
    db.refresh(user)
    return user


def delete_user(db: Session, user_id: str) -> bool:
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        return False
    db.delete(user)
    db.commit()
    return True


def ensure_admin_exists(db: Session) -> None:
    admin = db.query(User).filter(User.role == UserRole.admin).first()
    if not admin:
        create_user(db, username="admin", password="changeme", role=UserRole.admin)
