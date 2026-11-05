from decimal import Decimal
from typing import Annotated

from pydantic import Field

Money = Annotated[Decimal, Field(max_digits=18, decimal_places=2)]
