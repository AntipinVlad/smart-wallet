from enum import StrEnum


class ExpenseCategory(StrEnum):
    FOOD_GROCERIES = "food.groceries"
    FOOD_RESTAURANTS = "food.restaurants"
    FOOD_DELIVERY = "food.delivery"
    TRANSPORT_PUBLIC = "transport.public"
    TRANSPORT_TAXI = "transport.taxi"
    TRANSPORT_FUEL = "transport.fuel"
    TRANSPORT_PARKING = "transport.parking"
    TRANSPORT_CAR_SERVICE = "transport.car_service"
    HOUSING_RENT = "housing.rent"
    HOUSING_UTILITIES = "housing.utilities"
    HOUSING_INTERNET_PHONE = "housing.internet_phone"
    HOUSING_REPAIRS = "housing.repairs"
    HEALTH_PHARMACY = "health.pharmacy"
    HEALTH_DOCTOR = "health.doctor"
    HEALTH_INSURANCE = "health.insurance"
    SHOPPING_CLOTHES = "shopping.clothes"
    SHOPPING_ELECTRONICS = "shopping.electronics"
    SHOPPING_HOUSEHOLD = "shopping.household"
    ENTERTAINMENT_SUBSCRIPTIONS = "entertainment.subscriptions"
    ENTERTAINMENT_EVENTS = "entertainment.events"
    ENTERTAINMENT_HOBBIES = "entertainment.hobbies"
    EDUCATION_COURSES = "education.courses"
    EDUCATION_BOOKS = "education.books"
    TRAVEL_TICKETS = "travel.tickets"
    TRAVEL_ACCOMMODATION = "travel.accommodation"
    PERSONAL_CARE_BEAUTY = "personal_care.beauty"
    PERSONAL_CARE_SPORT = "personal_care.sport"
    GIFTS_GIFTS = "gifts.gifts"
    GIFTS_CHARITY = "gifts.charity"
    FINANCE_FEES = "finance.fees"
    FINANCE_TAXES = "finance.taxes"
    FINANCE_LOAN_PAYMENT = "finance.loan_payment"
    OTHER = "other.other"


class IncomeCategory(StrEnum):
    SALARY_SALARY = "salary.salary"
    SALARY_BONUS = "salary.bonus"
    BUSINESS_FREELANCE = "business.freelance"
    BUSINESS_SALES = "business.sales"
    INVESTMENTS_INTEREST = "investments.interest"
    INVESTMENTS_DIVIDENDS = "investments.dividends"
    GIFTS_GIFT = "gifts.gift"
    REFUNDS_REFUND = "refunds.refund"
    REFUNDS_CASHBACK = "refunds.cashback"
    OTHER = "other.other"


def split_category(value: ExpenseCategory | IncomeCategory) -> tuple[str, str]:
    category, _, subcategory = value.partition(".")
    return category, subcategory
