from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.enum import CurrencyEnum
from app.models import Wallet


async def is_wallet_exist(db: AsyncSession, user_id: int, wallet_name: str) -> bool:
    wallet_id = await db.scalar(select(Wallet.id).where(Wallet.name == wallet_name, Wallet.user_id == user_id))
    return wallet_id is not None


async def get_all_wallets(db: AsyncSession, user_id: int) -> list[Wallet]:
    return list(await db.scalars(select(Wallet).where(Wallet.user_id == user_id)))


async def get_wallet_by_name_for_update(db: AsyncSession, user_id: int, wallet_name: str) -> Wallet | None:
    return await db.scalar(
        select(Wallet)
        .where(Wallet.name == wallet_name, Wallet.user_id == user_id)
        .with_for_update()
    )


async def get_wallets_by_ids_for_update(db: AsyncSession, user_id: int, wallet_ids: list[int]) -> list[Wallet]:
    # order_by(Wallet.id): rows are always locked in the same order. Without it, transfers A -> B and B -> A
    # could each lock their source wallet and then wait forever for the other one (a deadlock).
    #
    # populate_existing: a transfer first reads the wallets without a lock, so they are already in the session.
    # By default SQLAlchemy would return those objects with the old balance and ignore the values of the
    # locked row. populate_existing overwrites them with the current values from the database.
    # It also overwrites changes that are not flushed yet, so call this before changing the wallets.
    return list(await db.scalars(
        select(Wallet)
        .where(Wallet.id.in_(wallet_ids), Wallet.user_id == user_id)
        .order_by(Wallet.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ))


async def create_wallet(db: AsyncSession, user_id: int, wallet_name: str, amount: Decimal,
                        currency: CurrencyEnum) -> Wallet:
    wallet = Wallet(name=wallet_name, balance=amount, user_id=user_id, currency=currency)
    db.add(wallet)
    await db.flush()
    return wallet


async def get_wallet_by_id(db: AsyncSession, user_id: int, wallet_id: int) -> Wallet | None:
    return await db.scalar(select(Wallet).where(Wallet.id == wallet_id, Wallet.user_id == user_id))
