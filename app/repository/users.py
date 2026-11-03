from sqlalchemy.orm import Session

from app.models import User

def get_user(db: Session, login: str) -> User:
    return db.query(User).filter(User.login == login).scalar()

def get_user_by_id(db: Session, user_id: int) -> User | None:
    return db.get(User, user_id)

def create_user(db: Session, login: str, password_hash: str) -> User:
    user = User(login=login, password_hash=password_hash)
    db.add(user)
    db.flush()
    return user
