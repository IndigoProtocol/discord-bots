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
import ssl
import certifi

ssl._create_default_https_context = lambda: ssl.create_default_context(cafile=certifi.where())

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
    url = 'https://analytics.indigoprotocol.io/api/v3/liquidations'
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
        'iSOL': 'https://cdn.discordapp.com/attachments/816779565796032513/859538870193881139/1311400287562366987/iSOLemoji.png',
    }

    if iasset_name in urls:
        return urls[iasset_name]
    else:
        return None


def get_iasset_emoji(iasset_name: str) -> str:
    discord_emojis = {
        'iUSD': '<:iUSDemoji:1230941267622367393>',
        'iBTC': '<:iBTCemoji:1230941348744401047>',
        'iETH': '<:iETHemoji:1230941175607722136>',
        'iSOL': '<:iSOLemoji:1311396708143464479>',
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


# Fiat-pegged iAssets trade as large whole-number amounts; crypto-pegged ones
# trade as small fractional amounts. This only affects how `iasset_burned` is
# formatted for display, not any math.
FIAT_IASSETS = frozenset({'iUSD', 'iEUR', 'iJPY'})

# Per-iAsset liquidation ratio (a.k.a. MCR) used to split absorbed collateral
# into debt repaid vs. surplus paid to Stability Pool stakers
# (`debt = collateral / ratio`). Governance currently sets every live iAsset to
# 110% (1.1); see the Liquidation Ratio governance polls. Centralized here so a
# ratio change is a one-line edit, and so an iAsset the protocol adds later
# doesn't silently use a wrong value.
LIQUIDATION_RATIO = {
    'iUSD': 1.1,
    'iEUR': 1.1,
    'iJPY': 1.1,
    'iBTC': 1.1,
    'iETH': 1.1,
    'iSOL': 1.1,
}

# Fallback for an iAsset not yet in LIQUIDATION_RATIO. 1.1 matches every live
# iAsset today, so a newly-launched iAsset renders sanely until its real ratio
# is added above. We log a warning rather than crash the bot.
DEFAULT_LIQUIDATION_RATIO = 1.1


def liquidation_ratio(iasset: str) -> float:
    '''Liquidation ratio (MCR) for an iAsset, with a safe default + warning.'''
    if iasset in LIQUIDATION_RATIO:
        return LIQUIDATION_RATIO[iasset]
    logging.getLogger('liquidations').warning(
        f'Unknown iAsset "{iasset}"; using default liquidation ratio '
        f'{DEFAULT_LIQUIDATION_RATIO}. Add it to LIQUIDATION_RATIO.'
    )
    return DEFAULT_LIQUIDATION_RATIO


def format_iasset_amount(iasset: str, amount: float) -> str:
    '''Human-friendly amount string, tuned to fiat vs. crypto iAsset magnitudes.'''
    if iasset in FIAT_IASSETS:
        # Large whole-number amounts (e.g. 12,345 iUSD).
        if amount >= 1000:
            return round_to_str(amount, 0)
        elif amount >= 1:
            return round_to_str(amount, 2)
        else:
            return f'{amount}'
    else:
        # Crypto-pegged: small fractional amounts (e.g. 0.0123 iBTC).
        if amount < 0.1:
            return round_to_str(amount, 6)
        else:
            return f'{amount}'


def liquidation_to_post_data(lq: dict) -> dict:
    iasset = lq["asset"]
    iasset_burned = float(lq['iasset_burned']) / 1_000_000
    # v3 prices collateral in USD. `collateral_absorbed` is in the collateral
    # asset's smallest unit (lovelaces for ADA, the only collateral so far).
    collateral_amount = float(lq['collateral_absorbed']) / 1_000_000
    collateral_usd_price = float(lq['collateral_asset_usd_price'])
    collateral_usd = collateral_amount * collateral_usd_price

    mcr = liquidation_ratio(iasset)
    iasset_burned_str = format_iasset_amount(iasset, iasset_burned)

    debt_usd = collateral_usd / mcr
    sp_staker_rewards = collateral_usd - debt_usd
    sp_staker_pct = sp_staker_rewards / collateral_usd * 100 if collateral_usd else 0

    msg = (
        f'- Burned: {get_iasset_emoji(iasset)}**{iasset_burned_str} {iasset}**\n'
        f'- Collateral: {get_fish_scale_emoji(collateral_usd)} **${round_to_str(collateral_usd, 2)}**\n'
        f'  - Debt: ${round_to_str(debt_usd, 2)}\n'
        f'  - {round_to_str(sp_staker_pct, 1)}% to {iasset} SP stakers: ${round_to_str(sp_staker_rewards, 2)}\n'
        f'- Collateral asset price: ${round_to_str(collateral_usd_price, 6)}\n'
        f'[cexplorer.io](<https://cexplorer.io/tx/{lq["output_hash"]}>)  ✧  '
        f'[adastat.net](<https://adastat.net/transactions/{lq["output_hash"]}>)  ✧  '
        f'[cardanoscan.io](<https://cardanoscan.io/transaction/{lq["output_hash"]}>)  ✧  '
        f'[explorer.cardano.org](https://explorer.cardano.org/en/transaction?id={lq["output_hash"]})'
    )

    post_data: dict[str, Any] = {
        'content': msg,
    }

    if collateral_usd >= 100_000:
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
    # collateral_asset_usd_price can be temporarily null while the Analytics API
    # settles a race condition; skip posting until it's populated.
    return bool(liquidation.get('collateral_asset_usd_price'))


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
                logger.info(f'New liquidation of {last_lq["asset"]}, id: {last_lq["id"]}')
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
