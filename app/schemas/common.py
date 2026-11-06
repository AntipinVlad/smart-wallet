from decimal import Decimal
from typing import Annotated

from pydantic import Field, StringConstraints

Money = Annotated[Decimal, Field(max_digits=18, decimal_places=2)]
PositiveMoney = Annotated[Money, Field(gt=0)]
NonNegativeMoney = Annotated[Money, Field(ge=0)]

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=127)]
