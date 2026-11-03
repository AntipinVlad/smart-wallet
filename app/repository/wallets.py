from decimal import Decimal
from sqlalchemy.orm import Session

from app.enum import CurrencyEnum
from app.models import Wallet


def is_wallet_exist(db: Session, user_id: int, wallet_name: str) -> bool:
    return db.query(Wallet).filter(Wallet.name == wallet_name, Wallet.user_id == user_id).first() is not None


def get_all_wallets(db: Session, user_id: int) -> list[Wallet]:
    return db.query(Wallet).filter(Wallet.user_id == user_id).all()


def get_wallet_by_name_for_update(db: Session, user_id: int, wallet_name: str) -> Wallet | None:
    return (
        db.query(Wallet)
        .filter(Wallet.name == wallet_name, Wallet.user_id == user_id)
        .with_for_update()
        .first()
    )


def get_wallets_by_ids_for_update(db: Session, user_id: int, wallet_ids: list[int]) -> list[Wallet]:
    # order_by(Wallet.id): rows are always locked in the same order. Without it, transfers A -> B and B -> A
    # could each lock their source wallet and then wait forever for the other one (a deadlock).
    #
    # populate_existing(): a transfer first reads the wallets without a lock, so they are already in the session.
    # By default SQLAlchemy would return those objects with the old balance and ignore the values of the
    # locked row. populate_existing() overwrites them with the current values from the database.
    # It also overwrites changes that are not flushed yet, so call this before changing the wallets.
    return (
        db.query(Wallet)
        .filter(Wallet.id.in_(wallet_ids), Wallet.user_id == user_id)
        .order_by(Wallet.id)
        .with_for_update()
        .populate_existing()
        .all()
    )


def create_wallet(db: Session, user_id: int, wallet_name: str, amount: Decimal, currency: CurrencyEnum) -> Wallet:
    wallet = Wallet(name=wallet_name, balance=amount, user_id=user_id, currency=currency)
    db.add(wallet)
    db.flush()
    return wallet


def get_wallet_by_id(db: Session, user_id: int, wallet_id: int) -> Wallet | None:
    return db.query(Wallet).filter(Wallet.id == wallet_id, Wallet.user_id == user_id).scalar()
