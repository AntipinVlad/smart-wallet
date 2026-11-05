from enum import StrEnum, auto


class CurrencyEnum(StrEnum):
    UAH = auto()
    USD = auto()
    EUR = auto()


class OperationType(StrEnum):
    EXPENSE = auto()
    INCOME = auto()
    TRANSFER = auto()
