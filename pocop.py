import http.client
import json
import logging
import os
import socket
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import List
from datetime import datetime

from dotenv import load_dotenv

# Load environment variables from .env file if it exists
load_dotenv()

# Create unverified SSL context
context = ssl._create_unverified_context()

WEBHOOK_URL = os.environ.get("WEBHOOK_URL")

# If webhook URL is not set, try to get it from command line arguments
if not WEBHOOK_URL and len(sys.argv) > 1:
    WEBHOOK_URL = sys.argv[1]

BASE_URL = "https://pocop.indigodao.org:2053"
POCOP_WEBSITE = "https://pocop.indigodao.org"


@dataclass
class PoCoPSubmission:
    link: str
    wallet: str
    date: str


def discord_comment(post_data: dict):
    """Send message to Discord webhook."""
    if not WEBHOOK_URL:
        raise Exception("WEBHOOK_URL not set")

    req = urllib.request.Request(
        WEBHOOK_URL,
        method="POST",
        data=json.dumps(post_data).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "User-Agent": "DiscordBot (private use) Python-urllib/3.11",
        },
    )

    urllib.request.urlopen(req, timeout=15, context=context)


def fetch_pocop_submissions(page: int = 1, limit: int = 10) -> dict:
    """Fetch PoCoP submissions from the API."""
    url = f"{BASE_URL}/json?page={page}&limit={limit}"

    headers = {
        "User-Agent": "DiscordBot (private use) Python-urllib/3.11",
        "Accept": "application/json",
    }

    req = urllib.request.Request(url, headers=headers)

    try:
        # Use unverified SSL context
        f = urllib.request.urlopen(req, timeout=15, context=context)
        response = f.read().decode("utf-8")

        # Debug information
        logger.debug(f"Request URL: {url}")
        logger.debug(f"Response status: {f.status}")
        logger.debug(f"Response headers: {f.headers}")

        return json.loads(response)
    except Exception as e:
        logger.error(f"Error fetching PoCoP submissions: {e}")
        return {}


def parse_submission(submission: dict) -> PoCoPSubmission:
    """Parse raw submission data into PoCoPSubmission object."""
    return PoCoPSubmission(
        link=submission.get("link", ""),
        wallet=submission.get("wallet", ""),
        date=submission.get("date", ""),
    )


def get_latest_submissions(
    num_pages: int = 3, limit: int = 10
) -> List[PoCoPSubmission]:
    """Fetch latest submissions from multiple pages."""
    all_submissions = []
    for page in range(1, num_pages + 1):
        response = fetch_pocop_submissions(page=page, limit=limit)
        if response and isinstance(response, dict) and "commits" in response:
            all_submissions.extend(
                [parse_submission(commit) for commit in response["commits"]]
            )
    return all_submissions


def submission_to_post_data(submission: PoCoPSubmission) -> dict:
    """Convert submission to Discord message format."""
    created_at = datetime.fromisoformat(submission.date.replace("Z", "+00:00"))
    formatted_date = created_at.strftime("%Y-%m-%d %H:%M:%S UTC")

    # Extract the platform (e.g., 'x.com' or 'twitter.com') from the link
    platform = (
        "𝕏"
        if "x.com" in submission.link
        else "Twitter" if "twitter.com" in submission.link else "🔗"
    )

    message = (
        f"🎨 **New Proof of Creative Participation**\n\n"
        f"**📅 Posted**: {formatted_date}\n"
        f"**👛 Wallet**:\n`{submission.wallet}`\n\n"
        f"**{platform} Post**: [View on X]({submission.link})\n"
        f"**🌐 View on PoCoP**: [Check Submission]({POCOP_WEBSITE})"
    )

    return {"content": message}


def setup_logging() -> logging.Logger:
    """Set up logging configuration."""
    logger = logging.getLogger("pocop_bot")
    logger.setLevel(logging.DEBUG)
    ch = logging.StreamHandler()
    ch.setLevel(logging.DEBUG)
    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)8s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )
    formatter.converter = time.gmtime
    ch.setFormatter(formatter)
    logger.addHandler(ch)
    return logger


def webhook_sanity_check():
    """Verify webhook URL is properly formatted."""
    if not WEBHOOK_URL:
        raise Exception("WEBHOOK_URL env var not set")
    elif not WEBHOOK_URL.startswith("https://discord.com/api/webhooks/"):
        raise Exception("WEBHOOK_URL isn't https://discord.com/api/webhooks/…")
    elif len(WEBHOOK_URL) != 121:
        raise Exception("WEBHOOK_URL length not 121")


if __name__ == "__main__":
    logger = setup_logging()

    try:
        webhook_sanity_check()
    except Exception as e:
        logger.error(e)
        sys.exit(1)

    # Track processed submission links
    processed_links = set()

    # Initial fetch and post all existing submissions
    initial_submissions = get_latest_submissions()
    logger.info(f"Found {len(initial_submissions)} initial submissions")

    # Post all initial submissions
    for submission in initial_submissions:
        if submission.link:  # Make sure link exists
            # logger.info(f'Posting initial submission: {submission.link}')
            # post_data = submission_to_post_data(submission)
            # discord_comment(post_data)
            # time.sleep(2)  # Rate limiting for Discord API
            processed_links.add(submission.link)

    logger.info(f"Initialized with {len(processed_links)} submission links")

    time.sleep(15)  # Sleep for 15 seconds before starting the main loop

    while True:
        try:
            # Fetch latest submissions
            current_submissions = get_latest_submissions()

            # Check for new submissions
            for submission in current_submissions:
                if submission.link and submission.link not in processed_links:
                    logger.info(f"New submission found: {submission.link}")
                    post_data = submission_to_post_data(submission)
                    discord_comment(post_data)
                    processed_links.add(submission.link)
                    time.sleep(2)  # Rate limiting for Discord API

            logger.info(
                f"Checked for new submissions. Total processed: {len(processed_links)}"
            )

        except http.client.RemoteDisconnected:
            logger.warning("Remote end closed connection without response")
        except urllib.error.HTTPError as e:
            logger.warning(f"HTTP Error occurred with status code: {e.code}")
        except urllib.error.URLError as e:
            logger.warning(f"URL Error occurred: {e.reason}")
        except http.client.HTTPException:
            logger.warning("HTTP Exception occurred")
        except socket.timeout:
            logger.warning("Socket Timeout occurred")
        except Exception as e:
            logger.error(f"Unexpected error: {e}")
        finally:
            time.sleep(120)  # Check every 2 minutes
