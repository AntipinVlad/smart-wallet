from enum import StrEnum, auto


class CurrencyEnum(StrEnum):
    UAH = auto()
    USD = auto()
    EUR = auto()
    # The most traded currencies after these (BIS survey): the exchange API has rates of all of them every day
    GBP = auto()
    JPY = auto()
    CHF = auto()
    CNY = auto()
    CAD = auto()
    AUD = auto()
    HKD = auto()
    SGD = auto()
    SEK = auto()
    NOK = auto()
    KRW = auto()
    NZD = auto()


class Language(StrEnum):
    UK = auto()
    EN = auto()


class OperationType(StrEnum):
    EXPENSE = auto()
    INCOME = auto()
    TRANSFER = auto()


class TransferDirection(StrEnum):
    OUTGOING = auto()
    INCOMING = auto()


class PeriodPreset(StrEnum):
    MONTH = auto()
    QUARTER = auto()
    YEAR = auto()


class TimelineStep(StrEnum):
    DAY = auto()
    WEEK = auto()
    MONTH = auto()


class TimelineSeries(StrEnum):
    INCOME = auto()
    EXPENSE = auto()
    NET = auto()


class ReportType(StrEnum):
    EXPENSE = auto()
    INCOME = auto()


class PeriodSeries(StrEnum):
    CURRENT = auto()
    PREVIOUS = auto()


class TipKind(StrEnum):
    OBSERVATION = auto()
    WARNING = auto()
    ADVICE = auto()
