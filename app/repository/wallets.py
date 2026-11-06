from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import CurrencyEnum
from app.models import Wallet


async def is_wallet_exist(db: AsyncSession, user_id: int, wallet_name: str) -> bool:
    wallet_id = await db.scalar(select(Wallet.id).where(Wallet.name == wallet_name, Wallet.user_id == user_id))
    return wallet_id is not None


async def get_all_wallets(db: AsyncSession, user_id: int) -> list[Wallet]:
    return list(await db.scalars(select(Wallet).where(Wallet.user_id == user_id)))


async def get_wallet_by_id_for_update(db: AsyncSession, user_id: int, wallet_id: int) -> Wallet | None:
    return await db.scalar(select(Wallet).where(Wallet.id == wallet_id, Wallet.user_id == user_id).with_for_update())


async def get_wallets_by_ids_for_update(db: AsyncSession, user_id: int, wallet_ids: list[int]) -> list[Wallet]:
    # order_by(id): always the same lock order, so transfers A -> B and B -> A cannot deadlock.
    # populate_existing: wallets already in the session get the locked values, unflushed changes are lost
    return list(
        await db.scalars(
            select(Wallet)
            .where(Wallet.id.in_(wallet_ids), Wallet.user_id == user_id)
            .order_by(Wallet.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    )


async def create_wallet(
    db: AsyncSession, user_id: int, wallet_name: str, amount: Decimal, currency: CurrencyEnum
) -> Wallet:
    wallet = Wallet(name=wallet_name, balance=amount, user_id=user_id, currency=currency)
    db.add(wallet)
    await db.flush()
    return wallet


async def get_wallet_by_id(db: AsyncSession, user_id: int, wallet_id: int) -> Wallet | None:
    return await db.scalar(select(Wallet).where(Wallet.id == wallet_id, Wallet.user_id == user_id))
