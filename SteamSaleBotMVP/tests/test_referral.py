from unittest.mock import AsyncMock, patch

import pytest

from app.services.referral import reward_referral_on_first_game


@pytest.mark.asyncio
async def test_skips_when_chat_already_had_games():
    bot = AsyncMock()

    with patch("app.services.referral.repository.try_reward_referral", AsyncMock()) as try_reward:
        await reward_referral_on_first_game(bot, 111, games_before_add=3)

    try_reward.assert_not_called()
    bot.send_message.assert_not_called()


@pytest.mark.asyncio
async def test_does_nothing_when_no_referrer_to_reward():
    bot = AsyncMock()

    with patch("app.services.referral.repository.try_reward_referral", AsyncMock(return_value=None)):
        await reward_referral_on_first_game(bot, 111, games_before_add=0)

    bot.send_message.assert_not_called()


@pytest.mark.asyncio
async def test_notifies_referrer_when_rewarded():
    bot = AsyncMock()

    with patch("app.services.referral.repository.try_reward_referral", AsyncMock(return_value=999)):
        await reward_referral_on_first_game(bot, 111, games_before_add=0)

    bot.send_message.assert_awaited_once()
    assert bot.send_message.await_args.args[0] == 999


@pytest.mark.asyncio
async def test_swallows_send_message_failure():
    bot = AsyncMock()
    bot.send_message.side_effect = Exception("boom")

    with patch("app.services.referral.repository.try_reward_referral", AsyncMock(return_value=999)):
        # не должно бросить исключение наружу — награда уже выдана в БД, сбой уведомления
        # реферера не должен ронять основной флоу добавления игры
        await reward_referral_on_first_game(bot, 111, games_before_add=0)
