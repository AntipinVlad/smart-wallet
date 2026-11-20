import re

# One Ukrainian and one English name per category, written from categories.py, not from search queries:
# labels picked to pass the evals would make the evals measure the labels
CATEGORY_LABELS: dict[str, tuple[str, ...]] = {
    "food": ("їжа", "food"),
    "food.groceries": ("продукти", "groceries"),
    "food.restaurants": ("ресторани", "restaurants"),
    "food.delivery": ("доставка їжі", "food delivery"),
    "transport": ("транспорт", "transport"),
    "transport.public": ("громадський транспорт", "public transport"),
    "transport.taxi": ("таксі", "taxi"),
    "transport.fuel": ("пальне", "fuel"),
    "transport.parking": ("паркування", "parking"),
    "transport.car_service": ("обслуговування авто", "car service"),
    "housing": ("житло", "housing"),
    "housing.rent": ("оренда", "rent"),
    "housing.utilities": ("комунальні послуги", "utilities"),
    "housing.internet_phone": ("інтернет і зв'язок", "internet and phone"),  # noqa: RUF001
    "housing.repairs": ("ремонт", "repairs"),
    "health": ("здоров'я", "health"),
    "health.pharmacy": ("аптека", "pharmacy"),
    "health.doctor": ("лікар", "doctor"),
    "health.insurance": ("страхування", "insurance"),
    "shopping": ("покупки", "shopping"),
    "shopping.clothes": ("одяг", "clothes"),
    "shopping.electronics": ("електроніка", "electronics"),
    "shopping.household": ("товари для дому", "household"),
    "entertainment": ("розваги", "entertainment"),
    "entertainment.subscriptions": ("підписки", "subscriptions"),
    "entertainment.events": ("події", "events"),
    "entertainment.hobbies": ("хобі", "hobbies"),  # noqa: RUF001
    "education": ("навчання", "education"),
    "education.courses": ("курси", "courses"),
    "education.books": ("книги", "books"),
    "travel": ("подорожі", "travel"),
    "travel.tickets": ("квитки", "tickets"),
    "travel.accommodation": ("проживання", "accommodation"),
    "personal_care": ("догляд за собою", "personal care"),
    "personal_care.beauty": ("краса", "beauty"),
    "personal_care.sport": ("спорт", "sport"),
    "gifts": ("подарунки", "gifts"),
    "gifts.gifts": ("подарунки", "gifts"),
    "gifts.charity": ("благодійність", "charity"),
    "gifts.gift": ("подарунки", "gifts"),
    "finance": ("фінанси", "finance"),
    "finance.fees": ("комісії", "fees"),
    "finance.taxes": ("податки", "taxes"),
    "finance.loan_payment": ("кредит", "loan payment"),
    "other": ("інше", "other"),
    "other.other": ("інше", "other"),
    "salary": ("зарплата", "salary"),
    "salary.salary": ("зарплата", "salary"),
    "salary.bonus": ("премія", "bonus"),
    "business": ("бізнес", "business"),
    "business.freelance": ("фриланс", "freelance"),
    "business.sales": ("продажі", "sales"),
    "investments": ("інвестиції", "investments"),
    "investments.interest": ("відсотки", "interest"),
    "investments.dividends": ("дивіденди", "dividends"),
    "refunds": ("повернення", "refunds"),
    "refunds.refund": ("повернення коштів", "refund"),
    "refunds.cashback": ("кешбек", "cashback"),
}

# Ukrainian text is typed with any of these apostrophes: the look-alikes are the point
APOSTROPHES = str.maketrans({"’": "'", "ʼ": "'", "`": "'"})  # noqa: RUF001


def normalize(text: str) -> str:
    return text.casefold().translate(APOSTROPHES)


def match_categories(query: str) -> list[tuple[str, str | None]]:
    """(category, subcategory) pairs whose label is a whole word or phrase of the query; None means any subcategory."""
    words = normalize(query)
    matched = {
        name
        for name, labels in CATEGORY_LABELS.items()
        if any(re.search(rf"(?<!\w){re.escape(normalize(label))}(?!\w)", words) for label in labels)
    }
    categories = {name for name in matched if "." not in name}
    # A matched category already covers its subcategories
    subcategories = {name for name in matched if "." in name and name.split(".")[0] not in categories}
    pairs: list[tuple[str, str | None]] = [(category, None) for category in sorted(categories)]
    for name in sorted(subcategories):
        category, _, subcategory = name.partition(".")
        pairs.append((category, subcategory))
    return pairs
