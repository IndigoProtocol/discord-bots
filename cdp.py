import datetime
import json
import logging
import math
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from enum import Enum, auto

WEBHOOK_URL = os.environ.get('WEBHOOK_URL')


class CdpEventType(Enum):
    OPEN = auto()
    CLOSE = auto()
    DEPOSIT = auto()
    WITHDRAW = auto()


@dataclass
class CdpEvent:
    type: CdpEventType
    ada: float
    new_collateral: float | None
    tvl: float
    iasset_name: str
    owner: str
    tx_id: str | None  # Closed account's final tx_id can't be discerned from API.


def discord_comment(msg: str):
    if not WEBHOOK_URL:
        raise Exception('WEBHOOK_URL not set')

    req = urllib.request.Request(
        WEBHOOK_URL,
        method='POST',
        data=json.dumps({'content': msg}).encode('utf-8'),
        headers={
            'Content-Type': 'application/json',
            # Discord only allows certain user-agents, others it'll block with 403
            # without explanation.
            # https://github.com/discord/discord-api-docs/issues/4908
            'User-Agent': 'DiscordBot (private use) Python-urllib/3.11',
        },
    )

    urllib.request.urlopen(req)


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
    elif ada < 2_000_000:
        return '🐳'
    elif ada >= 2_000_000:
        return '🐳' + math.floor(ada / 1_000_000) * '🚨'
    else:
        return ''


def event_to_discord_comment(event: CdpEvent) -> str:
    lines: list[str] = []

    iasset_emoji = get_iasset_emoji(event.iasset_name)

    if event.type == CdpEventType.OPEN:
        lines.append(f'{iasset_emoji} **CDP opened**')
    elif event.type == CdpEventType.CLOSE:
        lines.append(f'{iasset_emoji} **CDP closed**')
    elif event.type == CdpEventType.DEPOSIT:
        lines.append(f'**Deposit into {iasset_emoji} CDP**')
    elif event.type == CdpEventType.WITHDRAW:
        lines.append(f'**Withdrawal from {iasset_emoji} CDP**')

    sign = '+' if event.type in (CdpEventType.OPEN, CdpEventType.DEPOSIT) else '-'
    lines.append(f'• {sign}{event.ada:,.0f} ADA {get_fish_scale_emoji(event.ada)}')

    if event.new_collateral is not None and event.type in (
        CdpEventType.DEPOSIT,
        CdpEventType.WITHDRAW,
    ):
        pct_change = (event.ada / event.new_collateral) * 100
        pct_prec = 1 if pct_change < 1 else 0
        collateral = f'{event.new_collateral:,.0f}'
        lines.append(f'• New total: {collateral} ADA')
        lines.append(f'• Change: {pct_change:+.{pct_prec}f}%')

    if event.type in (CdpEventType.WITHDRAW, CdpEventType.CLOSE):
        tax = event.ada * 0.02
        lines.append(f'• 2% to INDY stakers: {tax:,.0f} ADA')

    lines.append(f'• New TVL: {event.tvl:,.0f} ADA')
    lines.append(f'• Owner PKH: `{event.owner}`')

    if event.type != CdpEventType.CLOSE:
        lines.append(
            f'[cexplorer.io](<https://cexplorer.io/tx/{event.tx_id}>)  ✧  '
            f'[adastat.net](<https://adastat.net/transactions/{event.tx_id}>)  ✧  '
            '[cardanoscan.io]'
            f'(<https://cardanoscan.io/transaction/{event.tx_id}>)  ✧  '
            '[explorer.cardano.org]'
            f'(https://explorer.cardano.org/en/transaction?id={event.tx_id})'
        )

    return '\n'.join(lines)


def fetch_cdps(at_unix_time: float | None = None):
    url = 'https://analytics.indigoprotocol.io/api/cdps'
    headers = {'Content-Type': 'application/json'}

    if at_unix_time is not None:
        params = {'timestamp': at_unix_time}
        data = json.dumps(params).encode('utf-8')
        req = urllib.request.Request(url, data=data, headers=headers, method='POST')
    else:
        req = urllib.request.Request(url)

    f = urllib.request.urlopen(req)
    response = f.read().decode('utf-8')
    json_response = json.loads(response)
    return json_response


def generate_cdp_events(old_list: list[dict], new_list: list[dict]) -> list[CdpEvent]:
    cdp_events = []

    old_dict = {(d['owner'], d['asset']): d for d in old_list}
    new_dict = {(d['owner'], d['asset']): d for d in new_list}

    tvl = sum([x['collateralAmount'] / 1e6 for x in new_list])

    for new_key, new_cdp in new_dict.items():
        if new_key not in old_dict:
            # OPEN event
            cdp_events.append(
                CdpEvent(
                    type=CdpEventType.OPEN,
                    ada=new_cdp['collateralAmount'] / 1e6,
                    new_collateral=new_cdp['collateralAmount'] / 1e6,
                    tvl=tvl,
                    iasset_name=new_cdp['asset'],
                    owner=new_cdp['owner'],
                    tx_id=new_cdp['output_hash'],
                )
            )
        else:
            old_cdp = old_dict[new_key]

            # DEPOSIT or WITHDRAW event
            if new_cdp['collateralAmount'] != old_cdp['collateralAmount']:
                event_type = (
                    CdpEventType.DEPOSIT
                    if new_cdp['collateralAmount'] > old_cdp['collateralAmount']
                    else CdpEventType.WITHDRAW
                )
                cdp_events.append(
                    CdpEvent(
                        type=event_type,
                        ada=abs(
                            new_cdp['collateralAmount'] - old_cdp['collateralAmount']
                        )
                        / 1e6,
                        tvl=tvl,
                        new_collateral=new_cdp['collateralAmount'] / 1e6,
                        iasset_name=new_cdp['asset'],
                        owner=new_cdp['owner'],
                        tx_id=new_cdp['output_hash'],
                    )
                )

    for old_key, old_cdp in old_dict.items():
        if old_key not in new_dict:
            # CLOSE event
            cdp_events.append(
                CdpEvent(
                    type=CdpEventType.CLOSE,
                    ada=old_cdp['collateralAmount'] / 1e6,
                    new_collateral=None,
                    tvl=tvl,
                    iasset_name=old_cdp['asset'],
                    owner=old_cdp['owner'],
                    tx_id=None,
                )
            )

    return cdp_events


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


def get_old_cdps(time_window: datetime.timedelta) -> list[dict]:
    now = datetime.datetime.utcnow()
    old_cdps = now - time_window
    return fetch_cdps(old_cdps.timestamp())


if __name__ == '__main__':
    logger = setup_logging()

    try:
        webhook_sanity_check()
    except Exception as e:
        logger.error(e)
        sys.exit(1)

    prev_cdps = fetch_cdps()
    logger.info(f'Fetched {len(prev_cdps)} initial CDPs')

    while True:
        try:
            time.sleep(30)
            cdps = fetch_cdps()
            events = generate_cdp_events(prev_cdps, cdps)

            if len(events) > 0:
                logger.info(f'Fetched {len(events)} new events')
            else:
                logger.debug(f'No new CDP events')

            if len(events) > 20:
                logger.error(
                    f'Suspiciously large number of events ({len(events)}), exiting'
                )
                sys.exit(1)

            prev_cdps = cdps

            for event in events:
                if event.ada >= 25_000:
                    logger.info(f'Discord commenting for {event.ada:,.0f} ADA event')
                    msg = event_to_discord_comment(event)
                    discord_comment(msg)
                    time.sleep(2)

        except urllib.error.URLError as err:
            logger.warning(err)