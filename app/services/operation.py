from datetime import datetime
from decimal import Decimal
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.enum import OperationType
from app.models import User
from app.schemas import OperationRequest, OperationResponse
from app.repository import wallets as wallet_repository
from app.repository import operations as operations_repository
from app.services.exchange_service import get_exchange_rate


def add_income(db: Session, current_user: User, operation: OperationRequest):
    if not wallet_repository.is_wallet_exist(db, current_user.id, operation.wallet_name):
        raise HTTPException(status_code=404, detail=f"Wallet '{operation.wallet_name}' not found")

    wallet = wallet_repository.add_income(db, current_user.id, operation.wallet_name, operation.amount)
    operation = operations_repository.create_operation(
        db=db,
        wallet_id=wallet.id,
        type=OperationType.INCOME,
        amount=operation.amount,
        currency=wallet.currency,
        description=operation.description
    )
    db.commit()

    return OperationResponse.model_validate(operation)


def add_expense(db: Session, current_user: User, operation: OperationRequest):
    if not wallet_repository.is_wallet_exist(db, current_user.id, operation.wallet_name):
        raise HTTPException(status_code=404, detail=f"Wallet '{operation.wallet_name}' not found")

    wallet = wallet_repository.get_wallet_balance_by_name(db, current_user.id, operation.wallet_name)
    if wallet.balance < operation.amount:
        raise HTTPException(status_code=400, detail=f"Insufficient funds. Available: {wallet.balance}")

    wallet = wallet_repository.add_expense(db, current_user.id, operation.wallet_name, operation.amount)
    operation = operations_repository.create_operation(
        db=db,
        wallet_id=wallet.id,
        type=OperationType.EXPENSE,
        amount=operation.amount,
        currency=wallet.currency,
        description=operation.description
    )
    db.commit()

    return OperationResponse.model_validate(operation)


def get_operations_list(
        db: Session,
        current_user: User,
        wallet_id: int | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None
) -> list[OperationResponse]:
    if wallet_id:
        wallet = wallet_repository.get_wallet_by_id(db, current_user.id, wallet_id)
        if not wallet:
            raise HTTPException(status_code=404, detail=f"Wallet '{wallet_id}' not found")

        wallets_ids = [wallet.id]
    else:
        wallets = wallet_repository.get_all_wallets(db, current_user.id)
        wallets_ids = [wallet.id for wallet in wallets]

    operations = operations_repository.get_operations_list(db, wallets_ids, date_from, date_to)
    return [OperationResponse.model_validate(operation) for operation in operations]


async def transfer_between_wallets(db: Session, user_id: int, from_wallet_id: int, to_wallet_id: int,
                                   amount: Decimal) -> list[OperationResponse]:
    from_wallet = wallet_repository.get_wallet_by_id(db, user_id, from_wallet_id)
    to_wallet = wallet_repository.get_wallet_by_id(db, user_id, to_wallet_id)

    if not from_wallet or not to_wallet:
        raise HTTPException(404, "Wallet not found")

    if from_wallet.balance < amount:
        raise HTTPException(400, f"Not enough funds. Available: {from_wallet.balance} {from_wallet.currency}")

    target_amount = amount
    if from_wallet.currency != to_wallet.currency:
        exchange_rate = await get_exchange_rate(from_wallet.currency, to_wallet.currency)
        target_amount = round(amount * exchange_rate, 2)

    from_wallet.balance = round(from_wallet.balance - amount, 2)
    to_wallet.balance = round(to_wallet.balance + target_amount, 2)
    transfer_id = str(uuid4())
    outgoing = operations_repository.create_operation(
        db=db,
        wallet_id=from_wallet.id,
        type=OperationType.TRANSFER,
        amount=amount,
        currency=from_wallet.currency,
        category="transfer",
        subcategory="outgoing",
        transfer_id=transfer_id,
        description=f"Transfer to {to_wallet.name}"
    )
    incoming = operations_repository.create_operation(
        db=db,
        wallet_id=to_wallet.id,
        type=OperationType.TRANSFER,
        amount=target_amount,
        currency=to_wallet.currency,
        category="transfer",
        subcategory="incoming",
        transfer_id=transfer_id,
        description=f"Transfer from {from_wallet.name}"
    )
    db.commit()
    return [OperationResponse.model_validate(outgoing), OperationResponse.model_validate(incoming)]
