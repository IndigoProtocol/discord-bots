import http
import json
import logging
import math
import os
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

WEBHOOK_URL = os.environ.get('WEBHOOK_URL')


class AnalyticsApiException(Exception):
    pass


def discord_comment(post_data: dict):
    if not WEBHOOK_URL:
        raise Exception('WEBHOOK_URL not set')

    req = urllib.request.Request(
        WEBHOOK_URL,
        method='POST',
        data=json.dumps(post_data).encode('utf-8'),
        headers={
            'Content-Type': 'application/json',
            # Discord only allows certain user-agents, others it'll block with 403
            # without explanation.
            # https://github.com/discord/discord-api-docs/issues/4908
            'User-Agent': 'DiscordBot (private use) Python-urllib/3.11',
        },
    )

    urllib.request.urlopen(req, timeout=15)


def fetch_liquidations(after_unix_time: int | None = None):
    url = 'https://analytics.indigoprotocol.io/api/liquidations'
    if after_unix_time:
        params = {'after': after_unix_time}
        query_string = urllib.parse.urlencode(params)
        url = url + f'?{query_string}'
    req = urllib.request.Request(url)
    f = urllib.request.urlopen(req, timeout=15)
    response = f.read().decode('utf-8')
    json_response = json.loads(response)
    return json_response


def slot_to_timestamp(slot: int) -> int:
    return slot - 4924800 + 1596491091


def timestamp_to_slot(unix_time: int) -> int:
    return unix_time - 1596491091 + 4924800


def get_fish_scale_emoji(ada: float) -> str:
    if not ada:
        return ''
    elif ada < 1000:
        return '🦐'
    elif ada < 10_000:
        return '🐟'
    elif ada < 100_000:
        return '🐬'
    elif ada < 1_000_000:
        return '🦈'
    elif ada >= 1_000_000:
        return '🐳' + math.floor(ada / 1_000_000) * '🚨'
    else:
        return ''


def get_iasset_icon_url(iasset_name: str) -> str | None:
    urls = {
        'iUSD': 'https://cdn.discordapp.com/attachments/859469846734307362/1097731509634482267/iUSDsmall.png',
        'iBTC': 'https://cdn.discordapp.com/attachments/859469846734307362/1097731510112632862/iBTCsmall.png',
        'iETH': 'https://cdn.discordapp.com/attachments/859469846734307362/1097731509856772136/iETHsmall.png',
    }

    if iasset_name in urls:
        return urls[iasset_name]
    else:
        return None


def get_iasset_emoji(iasset_name: str) -> str:
    discord_emojis = {
        'iUSD': '<:iUSDemoji:1058094170264309892>',
        'iBTC': '<:iBTCemoji:1058094192502509589>',
        'iETH': '<:iETHemoji:1058094251164057610>',
    }

    if iasset_name in discord_emojis:
        return discord_emojis[iasset_name] + ' '
    else:
        return ''


def round_to_str(num: float, precision: int) -> str:
    rounded = f'{num:,.{precision}f}'
    if precision == 0:
        return rounded
    else:
        return str(rounded).rstrip('0').rstrip('.')


def liquidation_to_post_data(lq: dict) -> dict:
    iasset = lq["asset"]
    iasset_burned = float(lq['iasset_burned']) / 1_000_000
    collateral_ada = float(lq['collateral_absorbed']) / 1_000_000
    oracle_price = float(lq['oracle_price'])

    if iasset == 'iUSD':
        price_main_prec = 3
        price_inverse_prec = 3
        mcr = 1.2
        if iasset_burned >= 1000:
            iasset_burned_str = round_to_str(iasset_burned, 0)
        elif iasset_burned >= 1:
            iasset_burned_str = round_to_str(iasset_burned, 2)
        else:
            iasset_burned_str = f'{iasset_burned}'
    elif iasset in ('iBTC', 'iETH'):
        price_main_prec = 8
        price_inverse_prec = 0
        mcr = 1.1
        if iasset_burned < 0.1:
            iasset_burned_str = round_to_str(iasset_burned, 6)
        else:
            iasset_burned_str = f'{iasset_burned}'
    else:
        raise AnalyticsApiException(f'Unexpected iasset "{iasset}"')

    collateral_nominal = collateral_ada / mcr
    indy_staker_rewards = 0.02 * collateral_ada
    sp_staker_rewards = collateral_ada - indy_staker_rewards - collateral_nominal
    sp_staker_pct = sp_staker_rewards / collateral_ada * 100

    msg = (
        f'- Burned: {get_iasset_emoji(iasset)}**{iasset_burned_str} {iasset}**\n'
        f'- Collateral: {get_fish_scale_emoji(collateral_ada)} **{round_to_str(collateral_ada, 2)} ADA**\n'
        f'  - Debt: {round_to_str(collateral_nominal, 2)} ADA\n'
        f'  - 2% to INDY stakers: {round_to_str(indy_staker_rewards, 2)} ADA\n'
        f'  - {round_to_str(sp_staker_pct, 1)}% to {iasset} SP stakers: {round_to_str(sp_staker_rewards, 2)} ADA\n'
        f'- Oracle price: {oracle_price:,.{price_inverse_prec}f} ADA/{iasset} '
        f'({1 / oracle_price:,.{price_main_prec}f} {iasset}/ADA)\n'
        f'[cexplorer.io](<https://cexplorer.io/tx/{lq["output_hash"]}>)  ✧  '
        f'[adastat.net](<https://adastat.net/transactions/{lq["output_hash"]}>)  ✧  '
        f'[cardanoscan.io](<https://cardanoscan.io/transaction/{lq["output_hash"]}>)  ✧  '
        f'[explorer.cardano.org](https://explorer.cardano.org/en/transaction?id={lq["output_hash"]})'
    )

    post_data: dict[str, Any] = {
        'content': msg,
    }

    if collateral_ada >= 100_000:
        post_data['embeds'] = [
            {
                'image': {
                    # Mortal Kombat "Finish Him", "Liquidated" meme, with Indigo logo.
                    'url': 'https://media.discordapp.net/attachments/816779566517321830/1108886710701523034/vllo.gif'
                }
            }
        ]

    return post_data


def sanity_check(liquidation: dict) -> bool:
    # oracle_price and ada_price from the liquidation dict can be temporarily
    # null. There's some weird race condition going on in the Analytics API.
    return bool(liquidation['oracle_price']) and bool(liquidation['ada_price'])


def check_liquidations(last_processed: dict) -> dict:
    '''Sends messages for any new liquidations.

    Returns:
        The last processed liquidation.
    '''
    local_last = last_processed

    try:
        new_lqs = fetch_liquidations(slot_to_timestamp(last_processed['slot']))
    except urllib.error.URLError as err:
        logger.warning(f'HTTP error (Analytics API): {err}')
        return local_last

    logger.debug(f'Fetched {len(new_lqs)} new liquidations from API')

    for lq in new_lqs:
        if not sanity_check(lq):
            logger.debug('Sanity check failed')
            return local_last
        if lq['id'] > last_processed['id']:
            try:
                discord_comment(liquidation_to_post_data(lq))
            except urllib.error.URLError as err:
                logger.warning(f'HTTP error (Discord webhook): {err}')
                return local_last
        if lq['id'] > local_last['id']:
            local_last = lq

    return local_last


def get_last(lqs: list[dict]) -> dict:
    return max(lqs, key=lambda x: x['id'])


def mock_last(lqs: list[dict], last_id: int) -> dict:
    return tuple(filter(lambda x: x['id'] == last_id, lqs))[0]


def setup_logging() -> logging.Logger:
    logger = logging.getLogger('liquidations')
    logger.setLevel(logging.DEBUG)
    ch = logging.StreamHandler()
    ch.setLevel(logging.DEBUG)
    formatter = logging.Formatter(
        '%(asctime)s [%(levelname)8s] %(message)s', datefmt='%Y-%m-%d %H:%M:%S'
    )
    formatter.converter = time.gmtime
    ch.setFormatter(formatter)
    logger.addHandler(ch)
    return logger


def webhook_sanity_check():
    if not WEBHOOK_URL:
        raise Exception('WEBHOOK_URL env var not set')
    elif not WEBHOOK_URL.startswith('https://discord.com/api/webhooks/'):
        raise Exception("WEBHOOK_URL isn't https://discord.com/api/webhooks/…")
    elif len(WEBHOOK_URL) != 121:
        raise Exception('WEBHOOK_URL length not 121')


if __name__ == '__main__':
    logger = setup_logging()

    try:
        webhook_sanity_check()
    except Exception as e:
        logger.error(e)
        sys.exit(1)

    last_lq = get_last(fetch_liquidations())

    while True:
        try:
            prev = last_lq
            last_lq = check_liquidations(last_lq)

            if prev != last_lq:
                logger.info(f'New liquidation, new last id: {last_lq["id"]}')
            else:
                logger.info(f'No new liquidations, last: {last_lq["id"]}')
        except http.client.RemoteDisconnected:
            logger.warning('Remote end closed connection without response')
        except urllib.error.HTTPError as e:
            logger.warning(f'HTTP error occurred with status code: {e.code}')
        except urllib.error.URLError as e:
            logger.warning(f'URL error occurred: {e}')
        except http.client.HTTPException:
            logger.warning('HTTP exception occurred')
        except socket.timeout:
            logger.warning('Socket Timeout occurred')
        except AnalyticsApiException as e:
            logger.error(e)
            sys.exit(1)
        finally:
            time.sleep(119)
