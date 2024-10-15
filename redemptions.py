import http.client
import json
import logging
import os
import socket
import ssl
import sys
import time
import urllib.request
import urllib.error
import urllib.parse
from dataclasses import dataclass

from dotenv import load_dotenv
from cdp import get_iasset_emoji, get_fish_scale_emoji

load_dotenv()

WEBHOOK_URL = os.environ.get('WEBHOOK_URL')
context = ssl._create_unverified_context()

if not WEBHOOK_URL:
    print("WEBHOOK_URL not set or couldn't be found")
else:
    print(f"Using WEBHOOK_URL: {WEBHOOK_URL}")


@dataclass
class RedemptionEvent:
    ada_redeemed: float
    interest: float
    asset_redeemed: float
    asset_name: str
    processing_fee: float
    tx_id: str


def discord_comment(post_data: dict):
    if not WEBHOOK_URL:
        raise Exception('WEBHOOK_URL not set')

    # Ensure unverified SSL context is used in Discord webhook POST request
    req = urllib.request.Request(
        WEBHOOK_URL,
        method='POST',
        data=json.dumps(post_data).encode('utf-8'),
        headers={
            'Content-Type': 'application/json',
            'User-Agent': 'DiscordBot (private use) Python-urllib/3.11',
        },
    )

    urllib.request.urlopen(req, timeout=15, context=context)


def fetch_redemptions():
    url = 'https://analytics.indigoprotocol.io/api/redemptions'
    req = urllib.request.Request(url)

    # Use the unverified SSL context in the API request
    f = urllib.request.urlopen(req, timeout=15, context=context)
    response = f.read().decode('utf-8')
    json_response = json.loads(response)
    return json_response


def redemption_to_discord_comment(event: RedemptionEvent) -> str:
    lines = []

    asset_emoji = get_iasset_emoji(event.asset_name)

    lines.append(f'{asset_emoji} {event.asset_name} Redemption')
    lines.append(f'- Redeemed: {event.asset_redeemed:,.2f} {event.asset_name}')
    lines.append(f'- ADA Redeemed: {event.ada_redeemed:,.2f} ADA {get_fish_scale_emoji(event.ada_redeemed)}')
    lines.append(f'- Interest Paid: {event.interest / 1e6:,.2f} ADA')
    lines.append(f'- Processing Fee: {event.processing_fee / 1e6:,.2f} ADA (to INDY Stakers)')

    lines.append(
        f'[cexplorer.io](<https://cexplorer.io/tx/{event.tx_id}>)  ✧  '
        f'[adastat.net](<https://adastat.net/transactions/{event.tx_id}>)  ✧  '
        '[cardanoscan.io]'
        f'(<https://cardanoscan.io/transaction/{event.tx_id}>)  ✧  '
        '[explorer.cardano.org]'
        f'(https://explorer.cardano.org/en/transaction?id={event.tx_id})'
    )

    return '\n'.join(lines)


def generate_redemption_events(old_list: list[str], new_list: list[dict]) -> list[RedemptionEvent]:
    redemption_events = []

    for new_redemption in new_list:
        if new_redemption['tx_hash'] not in old_list:  # Check against old_list which contains tx_hashes
            redemption_events.append(
                RedemptionEvent(
                    ada_redeemed=new_redemption['lovelaces_returned'] / 1e6,
                    interest=new_redemption['interest'],
                    asset_redeemed=new_redemption['redeemed_amount'] / 1e6,
                    asset_name=new_redemption['asset'],
                    processing_fee=new_redemption['processing_fee_lovelaces'] / 1e6,
                    tx_id=new_redemption['tx_hash'],
                )
            )
            old_list.append(new_redemption['tx_hash'])
    return redemption_events


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


def round_to_str(num: float, precision: int) -> str:
    rounded = f'{num:,.{precision}f}'
    if precision == 0:
        return rounded
    else:
        return str(rounded).rstrip('0').rstrip('.')


def redemption_to_post_data(event: RedemptionEvent) -> dict:
    iasset_emoji = get_iasset_emoji(event.asset_name)

    msg = (
        f'{iasset_emoji} **{event.asset_name} Redemption**\n'
        f'- Redeemed: {event.asset_redeemed:,.6f} {event.asset_name}\n'
        f'- ADA Redeemed: {round_to_str(event.ada_redeemed, 2)} ADA {get_fish_scale_emoji(event.ada_redeemed)}\n'
        f'- Interest Paid: {round_to_str(event.interest / 1e6, 2)} ADA\n'
        f'- Processing fee: {round_to_str(event.processing_fee, 2)} ADA (to INDY Stakers)\n\n'
        f'[cexplorer.io](<https://cexplorer.io/tx/{event.tx_id}>) ✧ '
        f'[adastat.net](<https://adastat.net/transactions/{event.tx_id}>) ✧ '
        f'[cardanoscan.io](<https://cardanoscan.io/transaction/{event.tx_id}>) ✧ '
        f'[explorer.cardano.org](<https://explorer.cardano.org/en/transaction?id={event.tx_id}>)'
    )

    return {'content': msg}


if __name__ == '__main__':
    logger = setup_logging()

    try:
        webhook_sanity_check()
    except Exception as e:
        logger.error(e)
        sys.exit(1)

    prev_redemptions = [r['tx_hash'] for r in fetch_redemptions()]
    logger.info(f'Fetched {len(prev_redemptions)} initial redemptions')

    while True:
        try:
            time.sleep(30)
            redemptions = fetch_redemptions()
            events = generate_redemption_events(prev_redemptions, redemptions)

            if len(events) > 0:
                logger.info(f'Fetched {len(events)} new redemption events')
                prev_redemptions += [new_event for new_event in redemptions if
                                     new_event not in prev_redemptions]
            else:
                logger.debug(f'No new redemption events')

            MIN_ADA_REDEEMED = 100
            for event in events:
                if event.ada_redeemed >= MIN_ADA_REDEEMED:
                    logger.info(f'Discord commenting for redemption event with {event.ada_redeemed} ADA for {event.asset_name}')
                    post_data = redemption_to_post_data(event)
                    discord_comment(post_data)
                    time.sleep(2)
                else:
                    logger.info(f'Redemption event with {event.ada_redeemed} ADA is below the threshold, not posting')

        except http.client.RemoteDisconnected:
            logger.warning('Remote end closed connection without response')
        except urllib.error.HTTPError as e:
            logger.warning(f'HTTP Error occurred with status code: {e.code}')
        except urllib.error.URLError as e:
            logger.warning(f'URL Error occurred: {e.reason}')
        except http.client.HTTPException:
            logger.warning('HTTP Exception occurred')
        except socket.timeout:
            logger.warning('Socket Timeout occurred')