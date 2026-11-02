from datetime import datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from app.enum import CurrencyEnum
from app.models import Operation


def create_operation(db: Session, wallet_id: int, type: str, amount: Decimal, currency: CurrencyEnum,
                     category: str | None = None, subcategory: str | None = None,
                     transfer_id: str | None = None, description: str | None = None) -> Operation:
    operation = Operation(
        wallet_id=wallet_id,
        type=type, amount=amount,
        currency=currency,
        category=category,
        subcategory=subcategory,
        transfer_id=transfer_id,
        description=description
    )
    db.add(operation)
    db.flush()
    return operation

def get_operations_list(
        db:Session,
        wallets_ids: list[int],
        date_from: datetime | None = None,
        date_to: datetime | None = None
) -> list[Operation]:
    query = db.query(Operation).filter(Operation.wallet_id.in_(wallets_ids))

    if date_from:
        query = query.filter(Operation.created_at >= date_from)

    if date_to:
        query = query.filter(Operation.created_at <= date_to)

    return query.all()