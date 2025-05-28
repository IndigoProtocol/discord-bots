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
from typing import List, Tuple
from datetime import datetime

from dotenv import load_dotenv

# Load environment variables from .env file if it exists
load_dotenv()

# Create unverified SSL context
context = ssl._create_unverified_context()

WEBHOOK_URL = os.environ.get("WEBHOOK_URL")
PROCESSED_LINKS_FILE = "processed_links.json"

# If webhook URL is not set, try to get it from command line arguments
if not WEBHOOK_URL and len(sys.argv) > 1:
    WEBHOOK_URL = sys.argv[1]

BASE_URL = "https://api2.indigodao.org/json"
POCOP_WEBSITE = "https://pocop.indigodao.org"


@dataclass
class PoCoPSubmission:
    link: str
    date: str
    category: str = ""
    views: int = 0


def setup_logging() -> logging.Logger:
    """Configure logger."""
    logger = logging.getLogger("pocop_bot")
    logger.setLevel(logging.DEBUG)
    handler = logging.StreamHandler()
    handler.setLevel(logging.DEBUG)
    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)8s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )
    formatter.converter = time.gmtime
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    return logger


logger = setup_logging()


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
    url = f"{BASE_URL}?page={page}&limit={limit}"

    headers = {
        "User-Agent": "DiscordBot (private use) Python-urllib/3.11",
        "Accept": "application/json",
    }

    req = urllib.request.Request(url, headers=headers)

    try:
        # Use unverified SSL context
        f = urllib.request.urlopen(req, timeout=15, context=context)
        response = f.read().decode("utf-8")
        return json.loads(response)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            logger.info("✅ No new submissions detected. Waiting for the next polling cycle.")
            return {}
        else:
            logger.error(f"HTTP Error while fetching PoCoP submissions: {e}")
            return {}
    except urllib.error.URLError as e:
        logger.error(f"URL Error while fetching PoCoP submissions: {e}")
        return {}
    except Exception as e:
        logger.error(f"Unexpected error while fetching PoCoP submissions: {e}")
        return {}


def parse_submission(submission: dict) -> PoCoPSubmission:
    """Parse raw submission data into PoCoPSubmission object."""
    # Handle None views from API
    views = submission.get("views", 0)
    if views is None:
        views = 0
    
    return PoCoPSubmission(
        link=submission.get("link", ""),
        date=submission.get("date", ""),
        category=submission.get("category", ""),
        views=views,
    )


def get_latest_submissions(limit: int = 10) -> List[PoCoPSubmission]:
    """Fetch all PoCoP submissions from the API."""
    submissions = []
    page = 1

    while True:
        try:
            response = fetch_pocop_submissions(page=page, limit=limit)
            if response and isinstance(response, dict) and "commits" in response:
                commits = [parse_submission(commit) for commit in response["commits"]]
                if not commits:
                    logger.info("No more commits found. Ending pagination.")
                    break
                submissions.extend(commits)
                page += 1
            else:
                logger.info("No more pages to fetch. Stopping pagination.")
                break

        except urllib.error.HTTPError as e:
            if e.code == 404:
                logger.warning("✅ No new submissions detected. Waiting for the next polling cycle.")
                break
            else:
                logger.error(f"HTTP Error occurred: {e}")
                break
        except Exception as e:
            logger.error(f"Unexpected error: {e}")
            break

    return submissions


def get_platform_info(link: str, category: str) -> Tuple[str, str]:
    """Get platform emoji and name based on link and category."""
    if "youtube.com" in link or "youtu.be" in link:
        return "📺", "YouTube"
    elif "x.com" in link:
        return "𝕏", "X (Twitter)"
    elif "twitter.com" in link:
        return "🐦", "Twitter"
    elif "instagram.com" in link:
        return "📷", "Instagram"
    elif "tiktok.com" in link:
        return "🎵", "TikTok"
    elif "linkedin.com" in link:
        return "💼", "LinkedIn"
    elif "reddit.com" in link:
        return "🔴", "Reddit"
    elif "medium.com" in link:
        return "📝", "Medium"
    elif "github.com" in link:
        return "🐙", "GitHub"
    elif category == "youtube":
        return "📺", "YouTube"
    elif category == "educational":
        return "📚", "Educational Content"
    else:
        return "🔗", "Web Link"


def submission_to_post_data(submission: PoCoPSubmission) -> dict:
    """Convert submission to Discord message format."""
    created_at = datetime.fromisoformat(submission.date.replace("Z", "+00:00"))
    formatted_date = created_at.strftime("%Y-%m-%d %H:%M:%S UTC")

    platform_emoji, platform_name = get_platform_info(submission.link, submission.category)
    
    # Add category info if available
    category_text = f" • **Category**: {submission.category.title()}" if submission.category else ""
    views_text = f" • **Views**: {submission.views}" if submission.views and submission.views > 0 else ""

    message = (
        f"🎨 **New Proof of Creative Participation**\n\n"
        f"**📅 Posted**: {formatted_date}\n"
        f"**{platform_emoji} Platform**: {platform_name}\n"
        f"**🔗 Content**: [View Submission]({submission.link})\n{category_text}{views_text}\n"
        f"**🌐 View on PoCoP**: [Check Submission]({POCOP_WEBSITE})"
    )

    return {"content": message}


def load_processed_links() -> set:
    """Load previously processed links from a file."""
    if os.path.exists(PROCESSED_LINKS_FILE):
        with open(PROCESSED_LINKS_FILE, "r") as file:
            return set(json.load(file))
    return set()


def save_processed_links(links: set):
    """Save processed links to a file."""
    with open(PROCESSED_LINKS_FILE, "w") as file:
        json.dump(list(links), file)


def webhook_sanity_check():
    """Verify webhook URL is properly formatted."""
    if not WEBHOOK_URL:
        raise Exception("WEBHOOK_URL env var not set")
    elif not WEBHOOK_URL.startswith("https://discord.com/api/webhooks/"):
        raise Exception("WEBHOOK_URL isn't https://discord.com/api/webhooks/…")
    elif len(WEBHOOK_URL) != 121:
        raise Exception("WEBHOOK_URL length not 121")


def main():
    """Main loop."""
    try:
        webhook_sanity_check()
    except Exception as e:
        logger.error(e)
        sys.exit(1)

    processed_links = load_processed_links()
    logger.info(f"Loaded {len(processed_links)} processed links.")

    while True:
        try:
            submissions = get_latest_submissions()
            new_submissions = [
                s for s in submissions if s.link and s.link not in processed_links
            ]

            # ✅ Sort submissions chronologically by date (oldest to newest)
            new_submissions.sort(key=lambda s: datetime.fromisoformat(s.date.replace("Z", "+00:00")))

            for submission in new_submissions:
                logger.info(f"New submission found: {submission.link} ({submission.category})")
                post_data = submission_to_post_data(submission)
                discord_comment(post_data)
                processed_links.add(submission.link)
                save_processed_links(processed_links)
                time.sleep(2)  # Discord rate limiting

            logger.info(
                f"Processed {len(new_submissions)} new submissions. Total: {len(processed_links)}"
            )

        except http.client.RemoteDisconnected:
            logger.warning("Remote end closed connection without response")
        except urllib.error.HTTPError as e:
            logger.warning(f"HTTP Error: {e.code}")
        except urllib.error.URLError as e:
            logger.warning(f"URL Error: {e.reason}")
        except http.client.HTTPException:
            logger.warning("HTTP Exception occurred")
        except socket.timeout:
            logger.warning("Socket Timeout occurred")
        except Exception as e:
            logger.error(f"Unexpected error: {e}")
        finally:
            logger.info("Sleeping for 120 seconds before the next cycle...")
            time.sleep(120)  # Wait 2 minutes before rechecking


if __name__ == "__main__":
    logger.info("Starting PoCoP Bot...")
    main()