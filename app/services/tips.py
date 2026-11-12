import logging

from openai import AsyncOpenAI
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.tips.cache import cache_key, cache_tips, cached_tips
from app.ai.tips.facts import build_facts
from app.ai.tips.service import TipsAnswer, write_tips
from app.models import User
from app.schemas import AnalyticsQuery, TipResponse, TipsResponse
from app.services import analytics as analytics_service
from app.services.exchange_rates import DailyRateLoader

logger = logging.getLogger(__name__)


async def get_tips(
    db: AsyncSession,
    rate_loader: DailyRateLoader,
    redis: Redis,
    openai_client: AsyncOpenAI,
    current_user: User,
    query: AnalyticsQuery,
) -> TipsResponse:
    summary = await analytics_service.get_summary(db, rate_loader, current_user, query)
    answer = None
    if summary.income or summary.expense:
        baseline = await analytics_service.get_baseline(db, rate_loader, current_user, query)
        facts = build_facts(summary, baseline)
        key = cache_key(current_user.id, facts, current_user.language)
        answer = await cached_tips(redis, key)
        if answer is None:
            answer = await write_tips(openai_client, facts, current_user.language)
            await cache_tips(redis, key, answer)
            logger.info("Tips of user %s written: %s cards", current_user.id, len(answer.tips))
        else:
            logger.info("Tips of user %s taken from cache", current_user.id)
    else:
        # Nothing to explain: a model call would only cost money
        logger.info("No tips for user %s, the period has no income or expenses", current_user.id)

    return TipsResponse(
        period=summary.period,
        currency=summary.currency,
        wallet_id=summary.wallet_id,
        language=current_user.language,
        summary=None if answer is None else answer.summary,
        tips=[] if answer is None else _cards(answer),
    )


def _cards(answer: TipsAnswer) -> list[TipResponse]:
    return [TipResponse(kind=tip.kind, category=tip.category, text=tip.text) for tip in answer.tips]
