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


@dataclass
class RedemptionEvent:
    ada_redeemed: float
    asset_redeemed: float
    asset_name: str
    redeemer: str
    tx_id: str


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


def fetch_redemptions():
    url = 'https://analytics.indigoprotocol.io/api/redemptions'
    req = urllib.request.Request(url)

    f = urllib.request.urlopen(req, timeout=15, context=context)
    response = f.read().decode('utf-8')
    json_response = json.loads(response)
    return json_response


def redemption_to_discord_comment(event: RedemptionEvent) -> str:
    lines = []

    asset_emoji = get_iasset_emoji(event.asset_name)

    lines.append(f'{asset_emoji} **Redemption**')
    lines.append(f'- Redeemed: {event.asset_redeemed:,.2f} {event.asset_name}')
    lines.append(f'- ADA Redeemed: {event.ada_redeemed:,.2f} ADA {get_fish_scale_emoji(event.ada_redeemed)}')
    lines.append(f'- Redeemer: `{event.redeemer}`')

    lines.append(
        f'[cexplorer.io](<https://cexplorer.io/tx/{event.tx_id}>)  ✧  '
        f'[adastat.net](<https://adastat.net/transactions/{event.tx_id}>)  ✧  '
        '[cardanoscan.io]'
        f'(<https://cardanoscan.io/transaction/{event.tx_id}>)  ✧  '
        '[explorer.cardano.org]'
        f'(https://explorer.cardano.org/en/transaction?id={event.tx_id})'
    )

    return '\n'.join(lines)


def generate_redemption_events(old_list: list[dict], new_list: list[dict]) -> list[RedemptionEvent]:
    redemption_events = []

    for new_redemption in new_list:
        if new_redemption not in old_list:
            redemption_events.append(
                RedemptionEvent(
                    ada_redeemed=new_redemption['ada_redeemed'] / 1e6,
                    asset_redeemed=new_redemption['asset_redeemed'] / 1e6,
                    asset_name=new_redemption['asset_name'],
                    redeemer=new_redemption['redeemer'],
                    tx_id=new_redemption['tx_id'],
                )
            )

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


if __name__ == '__main__':
    logger = setup_logging()

    try:
        webhook_sanity_check()
    except Exception as e:
        logger.error(e)
        sys.exit(1)

    prev_redemptions = fetch_redemptions()
    logger.info(f'Fetched {len(prev_redemptions)} initial redemptions')

    while True:
        try:
            time.sleep(30)
            redemptions = fetch_redemptions()
            events = generate_redemption_events(prev_redemptions, redemptions)

            if len(events) > 0:
                logger.info(f'Fetched {len(events)} new redemption events')
            else:
                logger.debug(f'No new redemption events')

            prev_redemptions = redemptions

            for event in events:
                logger.info(f'Discord commenting for redemption event')
                msg = redemption_to_discord_comment(event)
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