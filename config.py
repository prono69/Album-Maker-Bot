import os
import logging

# Configure Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

API_ID = 123456  # Replace with your API ID
API_HASH = "your_api_hash"
# Bot Token
BOT_TOKEN = os.environ.get("BOT_TOKEN", "6556dtg")
if not BOT_TOKEN:
    LOGGER.warning("BOT_TOKEN is not set. The bot will not start.")

# Threshold: How many items before auto-sending immediately? (Max 10)
try:
    AUTO_SEND_THRESHOLD = int(os.environ.get("AUTO_SEND_THRESHOLD", "10"))
    if AUTO_SEND_THRESHOLD > 10:
        AUTO_SEND_THRESHOLD = 10  # Telegram hard limit
except ValueError:
    AUTO_SEND_THRESHOLD = 10

# Delay: Seconds to wait for more photos before sending
try:
    AUTO_SEND_DELAY = float(os.environ.get("AUTO_SEND_DELAY", "3"))
except ValueError:
    AUTO_SEND_DELAY = 3.0
