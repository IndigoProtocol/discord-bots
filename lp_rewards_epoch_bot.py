#!/usr/bin/env python3
"""
Discord bot for posting LP rewards breakdown at epoch crossings.
Fetches LP distribution from Indigo Protocol API and posts formatted messages.
"""

import json
import os
import sys
import time
import urllib.request
import urllib.error
import ssl
import certifi
from datetime import datetime
from typing import Dict, Any, Optional
from dotenv import load_dotenv

load_dotenv()

# Create SSL context with proper certificate verification
ssl_context = ssl.create_default_context(cafile=certifi.where())

WEBHOOK_URL = os.environ.get("WEBHOOK_URL")
EPOCH_TRACKING_FILE = "last_posted_epoch.json"
LP_DISTRIBUTION_API = "https://config.indigoprotocol.io/mainnet/lp-distribution.json"

# Cardano epoch parameters
GENESIS_BLOCK_START_TIME_SECONDS = 1506203091  # Cardano mainnet genesis
EPOCH_LENGTH_SECONDS = 432000  # 5 days

# Stability Pool rewards (hardcoded as per requirements)
STABILITY_POOL_REWARDS = {
    "iUSD": 5250,
    "iBTC": 1800,
    "iETH": 500,
    "iSOL": 40
}

# Other rewards
INDY_STAKING = 10000
PREVIOUS_TOTAL = 15960


def get_current_epoch() -> int:
    """Calculate current Cardano epoch."""
    current_time_seconds = int(time.time())
    return (current_time_seconds - GENESIS_BLOCK_START_TIME_SECONDS) // EPOCH_LENGTH_SECONDS


def load_last_posted_epoch() -> Optional[int]:
    """Load the last epoch number that was posted."""
    if os.path.exists(EPOCH_TRACKING_FILE):
        try:
            with open(EPOCH_TRACKING_FILE, 'r') as f:
                data = json.load(f)
                return data.get('last_posted_epoch')
        except (json.JSONDecodeError, FileNotFoundError):
            return None
    return None


def save_last_posted_epoch(epoch: int):
    """Save the epoch number that was just posted."""
    with open(EPOCH_TRACKING_FILE, 'w') as f:
        json.dump({'last_posted_epoch': epoch, 'timestamp': datetime.utcnow().isoformat()}, f)


def fetch_lp_distribution() -> Dict[str, Any]:
    """Fetch LP distribution from Indigo Protocol API."""
    try:
        req = urllib.request.Request(
            LP_DISTRIBUTION_API,
            headers={'User-Agent': 'DiscordBot (private use) Python-urllib/3.11'}
        )
        with urllib.request.urlopen(req, timeout=15, context=ssl_context) as response:
            return json.loads(response.read().decode('utf-8'))
    except (urllib.error.URLError, json.JSONDecodeError) as e:
        raise Exception(f"Failed to fetch LP distribution from API: {e}")


def calculate_totals(lp_distribution: Dict[str, Any]) -> tuple:
    """Calculate total LP rewards and overall totals."""
    lp_total = 0
    for dex, pairs in lp_distribution.items():
        for pair, amount in pairs.items():
            lp_total += amount
    
    sp_total = sum(STABILITY_POOL_REWARDS.values())
    total_iassets = lp_total + sp_total
    
    return lp_total, sp_total, total_iassets


def format_rewards_message(lp_distribution: Dict[str, Any], epoch: int) -> str:
    """Format the rewards breakdown message for Discord."""
    lp_total, sp_total, total_iassets = calculate_totals(lp_distribution)
    
    message = f"""**Epoch {epoch} Rewards Distribution**

**Total iAssets Incentives**
{total_iassets:,} INDY per epoch
Previous {PREVIOUS_TOTAL:,} INDY

**Stability Pool ({sp_total:,} INDY)**
"""
    
    for asset, amount in STABILITY_POOL_REWARDS.items():
        message += f"{asset}: {amount:,} INDY\n"
    
    message += f"\n**Liquidity Pools ({lp_total:,} INDY)**\n"
    
    for dex, pairs in lp_distribution.items():
        # Format DEX name properly
        dex_name = "Minswap" if dex == "MinSwap" else "Sundaeswap" if dex == "SundaeSwap" else dex
        message += f"{dex_name}:\n"
        for pair, amount in pairs.items():
            message += f"{pair}: {amount:,} INDY\n"
        message += "\n"
    
    message += f"""**Total INDY Incentives**
{INDY_STAKING:,} INDY per epoch
No Changes

**INDY Staking**
{INDY_STAKING:,} INDY

**Rationale for Incentive Adjustments**
These emissions adjustments support our commitment to optimal SP/LP health and sustainability going forward. No major rebalancing between SP and LP incentives are in play yet this adjustment will improve tokenomics aligning with the DAO's long term goals for the protocol."""
    
    return message


def send_discord_message(message: str):
    """Send message to Discord webhook."""
    if not WEBHOOK_URL:
        raise Exception("WEBHOOK_URL not set")
    
    post_data = {
        'content': message,
        'username': 'LP Rewards Bot'
    }
    
    req = urllib.request.Request(
        WEBHOOK_URL,
        method='POST',
        data=json.dumps(post_data).encode('utf-8'),
        headers={
            'Content-Type': 'application/json',
            'User-Agent': 'DiscordBot (private use) Python-urllib/3.11',
        },
    )
    
    try:
        urllib.request.urlopen(req, timeout=15, context=ssl_context)
        print(f"Successfully posted epoch rewards message")
    except urllib.error.URLError as e:
        print(f"Error posting to Discord: {e}")
        raise


def main():
    """Main bot loop."""
    if not WEBHOOK_URL:
        print("Error: WEBHOOK_URL environment variable not set")
        print("Usage: WEBHOOK_URL='https://discord.com/api/webhooks/...' python3 lp_rewards_epoch_bot.py")
        sys.exit(1)
    
    print(f"LP Rewards Epoch Bot started")
    print(f"Checking for epoch changes...")
    
    while True:
        try:
            current_epoch = get_current_epoch()
            last_posted_epoch = load_last_posted_epoch()
            
            print(f"Current epoch: {current_epoch}, Last posted: {last_posted_epoch}")
            
            if last_posted_epoch is None or current_epoch > last_posted_epoch:
                print(f"New epoch detected! Posting rewards for epoch {current_epoch}")
                
                try:
                    # Fetch latest LP distribution
                    lp_distribution = fetch_lp_distribution()
                    
                    # Format and send message
                    message = format_rewards_message(lp_distribution, current_epoch)
                    send_discord_message(message)
                    
                    # Save the epoch as posted
                    save_last_posted_epoch(current_epoch)
                    print(f"Saved epoch {current_epoch} as posted")
                    
                except Exception as e:
                    print(f"Error posting epoch rewards: {e}")
                    print("Will retry in 10 minutes...")
                    # Don't save epoch as posted, retry in 10 minutes
                    time.sleep(600)
                    continue
            
            # Wait 1 hour before checking again
            # Epochs change every 5 days, so checking hourly is more than sufficient
            time.sleep(3600)
            
        except KeyboardInterrupt:
            print("\nBot stopped by user")
            break
        except Exception as e:
            print(f"Error in main loop: {e}")
            # Wait a bit before retrying
            time.sleep(60)


if __name__ == "__main__":
    main()