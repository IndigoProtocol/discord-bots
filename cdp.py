import datetime
import gzip
import http.client
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
from dataclasses import dataclass
from enum import Enum, auto
import ssl
import certifi

ssl._create_default_https_context = lambda: ssl.create_default_context(cafile=certifi.where())

import jsonschema

WEBHOOK_URL = os.environ.get('WEBHOOK_URL')


class CdpEventType(Enum):
    OPEN = auto()
    CLOSE = auto()
    DEPOSIT = auto()
    WITHDRAW = auto()
    FREEZE = auto()
    MERGE = auto()


@dataclass
class CdpEvent:
    type: CdpEventType
    ada: float
    new_collateral: float | None
    tvl: float
    iasset_name: str
    debt: float
    owner: str | None  # Merge events don't have an owner.
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

    urllib.request.urlopen(req, timeout=15)


def get_iasset_emoji(iasset_name: str) -> str:
    discord_emojis = {
        'iUSD': '<:iUSDemoji:1230941267622367393>',
        'iBTC': '<:iBTCemoji:1230941348744401047>',
        'iETH': '<:iETHemoji:1230941175607722136>',
        'iSOL': '<:iSOLemoji:131139670814346479>'
    }

    if iasset_name in discord_emojis:
        return discord_emojis[iasset_name]
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
    elif ada >= 1_000_000:
        return '🐳' + math.floor(ada / 1_000_000) * '🚨'
    else:
        return ''


def round_to_str(num: float, precision: int) -> str:
    rounded = f'{num:,.{precision}f}'
    if precision == 0:
        return rounded
    else:
        return str(rounded).rstrip('0').rstrip('.')


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
    elif event.type == CdpEventType.FREEZE:
        lines.append(f'**{iasset_emoji} CDP frozen** ❄️')
    elif event.type == CdpEventType.MERGE:
        lines.append(f'**Frozen {iasset_emoji} CDPs merged** ↔️')

    sign = '+' if event.type in (CdpEventType.OPEN, CdpEventType.DEPOSIT) else '-'
    lines.append(f'- {sign}{event.ada:,.0f} ADA {get_fish_scale_emoji(event.ada)}')

    if event.debt >= 1000:
        debt_str = round_to_str(event.debt, 0)
    elif event.debt >= 1:
        debt_str = round_to_str(event.debt, 2)
    else:
        debt_str = f'{event.debt}'

    if event.type != CdpEventType.MERGE:
        lines.append(f'- Debt: {debt_str} {event.iasset_name}')

    if event.type not in (CdpEventType.FREEZE, CdpEventType.MERGE):
        if event.new_collateral is not None and event.type in (
            CdpEventType.DEPOSIT,
            CdpEventType.WITHDRAW,
        ):
            if event.type == CdpEventType.DEPOSIT:
                pct_change = (event.ada / event.new_collateral) * 100
            else:
                pct_change = -1 * event.ada / (event.ada + event.new_collateral) * 100
            pct_prec = 0 if 1 <= abs(pct_change) <= 99 else 1
            collateral = f'{event.new_collateral:,.0f}'
            lines.append(f'- New collateral: {collateral} ADA')
            lines.append(f'- Change: {pct_change:+.{pct_prec}f}%')

        # if event.type in (CdpEventType.WITHDRAW, CdpEventType.CLOSE):
        #     tax = event.ada * 0.02
        #     lines.append(f'- 2% to INDY stakers: {tax:,.0f} ADA')

        lines.append(f'- New TVL: {event.tvl:,.0f} ADA')

    if event.type != CdpEventType.MERGE:
        lines.append(f'- Owner PKH: `{event.owner}`')

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


def fetch_cdps(log_dir: str, at_unix_time: float | None = None):
    url = 'https://analytics.indigoprotocol.io/api/cdps'
    headers = {'Content-Type': 'application/json'}

    if at_unix_time is not None:
        params = {'timestamp': at_unix_time}
        data = json.dumps(params).encode('utf-8')
        req = urllib.request.Request(url, data=data, headers=headers, method='POST')
    else:
        req = urllib.request.Request(url)

    f = urllib.request.urlopen(req, timeout=15)
    response = f.read().decode('utf-8')
    json_response = json.loads(response)

    if at_unix_time is not None:
        dt = datetime.datetime.fromtimestamp(at_unix_time)
    else:
        dt = datetime.datetime.now()

    err = validate_cdps_json(json_response)
    if err is not None:
        log_file = (
            dt.strftime('%Y-%m-%d-%H-%M-%S-') + str(int(dt.timestamp())) + '.json.gz'
        )
        logger.error('/cdps response not valid against schema')
        logger.error(err)
        logger.error(f'Log of invalid JSON: {log_file}')
        print(err)

        with gzip.open(os.path.join(log_dir, log_file), 'wt') as log_file:
            json.dump(json_response, log_file, indent=4)

    return json_response


def validate_cdps_json(json_response):
    with open('cdps-schema.json') as f:
        schema = json.load(f)
    try:
        jsonschema.validate(json_response, schema)
        return None
    except jsonschema.exceptions.ValidationError as e:
        return e


def generate_cdp_events(old_list: list[dict], new_list: list[dict]) -> list[CdpEvent]:
    cdp_events = []

    # Separate the CDPs with owners and without owners
    old_with_owner = [d for d in old_list if d['owner'] is not None]
    old_without_owner = [d for d in old_list if d['owner'] is None]
    new_with_owner = [d for d in new_list if d['owner'] is not None]
    new_without_owner = [d for d in new_list if d['owner'] is None]

    # Create dictionaries for CDPs with owners
    old_dict_with_owner = {(d['owner'], d['asset']): d for d in old_with_owner}
    new_dict_with_owner = {(d['owner'], d['asset']): d for d in new_with_owner}

    # Create dictionaries for CDPs without owners, linking them based on collateralAmount, mintedAmount, and asset
    old_dict_without_owner = {
        (d['collateralAmount'], d['mintedAmount'], d['asset']): d
        for d in old_without_owner
    }
    new_dict_without_owner = {
        (d['collateralAmount'], d['mintedAmount'], d['asset']): d
        for d in new_without_owner
    }

    tvl = sum([x['collateralAmount'] / 1e6 for x in new_list])

    # Process CDPs with owners
    for new_key, new_cdp in new_dict_with_owner.items():
        if new_key not in old_dict_with_owner and new_cdp['owner'] != 'NULL':
            # OPEN event
            cdp_events.append(create_cdp_event(CdpEventType.OPEN, new_cdp, tvl))
        else:
            old_cdp = old_dict_with_owner[new_key]
            create_deposit_withdraw_or_freeze_event(old_cdp, new_cdp, tvl, cdp_events)

    frozen: dict[tuple[str, str], bool] = {}

    # Process CDPs without owners
    for new_key, new_cdp in new_dict_without_owner.items():
        if new_key not in old_dict_without_owner:
            old_cdp = find_corresponding_cdp_with_owner(old_with_owner, new_cdp)
            if old_cdp is not None:
                # FREEZE event
                frozen[(old_cdp['owner'], old_cdp['asset'])] = True
                cdp_events.append(
                    create_cdp_event(
                        CdpEventType.FREEZE,
                        old_cdp,
                        tvl,
                        new_collateral=new_cdp['collateralAmount'],
                        tx_id=new_cdp['output_hash'],
                    )
                )

    for old_key, old_cdp in old_dict_with_owner.items():
        if old_key in frozen:
            continue
        if old_key not in new_dict_with_owner:
            # CLOSE event
            cdp_events.append(
                create_cdp_event(
                    CdpEventType.CLOSE, old_cdp, tvl, new_collateral=None, tx_id=None
                )
            )

    return cdp_events


def create_cdp_event(event_type, cdp, tvl, new_collateral=None, tx_id=None):
    return CdpEvent(
        type=event_type,
        ada=cdp['collateralAmount'] / 1e6,
        new_collateral=new_collateral
        if new_collateral is not None
        else cdp['collateralAmount'] / 1e6,
        tvl=tvl,
        iasset_name=cdp['asset'],
        debt=cdp['mintedAmount'] / 1e6,
        owner=cdp['owner'],
        tx_id=tx_id if tx_id is not None else cdp['output_hash'],
    )


def create_deposit_withdraw_or_freeze_event(old_cdp, new_cdp, tvl, cdp_events):
    if new_cdp['collateralAmount'] != old_cdp['collateralAmount']:
        event_type = (
            CdpEventType.DEPOSIT
            if new_cdp['collateralAmount'] > old_cdp['collateralAmount']
            else CdpEventType.WITHDRAW
        )
        cdp_events.append(
            CdpEvent(
                type=event_type,
                ada=abs(new_cdp['collateralAmount'] - old_cdp['collateralAmount'])
                / 1e6,
                tvl=tvl,
                new_collateral=new_cdp['collateralAmount'] / 1e6,
                iasset_name=new_cdp['asset'],
                debt=new_cdp['mintedAmount'] / 1e6,
                owner=new_cdp['owner'],
                tx_id=new_cdp['output_hash'],
            )
        )
    elif new_cdp['owner'] is None and old_cdp['owner'] is not None:
        # MERGE event
        if old_cdp['owner'] == '' or old_cdp['owner'] == 'NULL':
            cdp_events.append(
                CdpEvent(
                    type=CdpEventType.MERGE,
                    ada=old_cdp['collateralAmount'] / 1e6,
                    new_collateral=new_cdp['collateralAmount'] / 1e6,
                    tvl=tvl,
                    iasset_name=old_cdp['asset'],
                    debt=old_cdp['mintedAmount'] / 1e6,
                    owner=None,
                    tx_id=old_cdp['output_hash'],
                )
            )

        # FREEZE event
        cdp_events.append(
            CdpEvent(
                type=CdpEventType.FREEZE,
                ada=old_cdp['collateralAmount'] / 1e6,
                new_collateral=new_cdp['collateralAmount'] / 1e6,
                tvl=tvl,
                iasset_name=old_cdp['asset'],
                debt=old_cdp['mintedAmount'] / 1e6,
                owner=old_cdp['owner'],
                tx_id=old_cdp['output_hash'],
            )
        )


def find_corresponding_cdp_with_owner(cdp_list, cdp_without_owner):
    for cdp in cdp_list:
        if (
            cdp['collateralAmount'] == cdp_without_owner['collateralAmount']
            and cdp['mintedAmount'] == cdp_without_owner['mintedAmount']
            and cdp['asset'] == cdp_without_owner['asset']
        ):
            return cdp
    return None


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


def get_old_cdps(log_dir: str, time_window: datetime.timedelta) -> list[dict]:
    now = datetime.datetime.utcnow()
    old_cdps = now - time_window
    return fetch_cdps(log_dir, old_cdps.timestamp())


if __name__ == '__main__':
    logger = setup_logging()

    try:
        webhook_sanity_check()
    except Exception as e:
        logger.error(e)
        sys.exit(1)

    log_dir = '/srv/cdp-log'
    logger.info(f'Logging JSON responses to {log_dir}')

    prev_cdps = fetch_cdps(log_dir)
    logger.info(f'Fetched {len(prev_cdps)} initial CDPs')

    while True:
        try:
            time.sleep(30)
            cdps = fetch_cdps(log_dir)
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

        except http.client.RemoteDisconnected:
            logger.warning('Remote end closed connection without response')
        except urllib.error.HTTPError as e:
            logger.warning(f'HTTP Error occurred with status code: {e.code}')
        except urllib.error.URLError:
            logger.warning('URL Error occurred')
        except http.client.HTTPException:
            logger.warning('HTTP Exception occurred')
        except socket.timeout:
            logger.warning('Socket Timeout occurred')
