from app.ai.categorization.categories import ExpenseCategory, IncomeCategory
from app.services.category_labels import CATEGORY_LABELS, match_categories

ALL_CATEGORIES = [*ExpenseCategory, *IncomeCategory]


def test_every_category_and_subcategory_has_a_label():
    # Labels come from the category list, not from the eval queries: a missing one is a gap, not a choice
    names = {category.value for category in ALL_CATEGORIES} | {category.split(".")[0] for category in ALL_CATEGORIES}

    assert names - CATEGORY_LABELS.keys() == set()


def test_a_label_inside_the_query_matches_its_category():
    assert match_categories("подарунки мамі") == [("gifts", None)]


def test_a_subcategory_label_matches_only_that_subcategory():
    assert match_categories("аптека") == [("health", "pharmacy")]


def test_a_label_must_be_a_whole_word_of_the_query():
    assert match_categories("автотранспортний") == []
