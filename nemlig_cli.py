#!/usr/bin/env python3
# PYTHON_ARGCOMPLETE_OK
"""
Nemlig.com CLI - A command-line interface for nemlig.com grocery shopping.

Usage:
    python nemlig_cli.py search "cocio"
    python nemlig_cli.py details PRODUCT_ID
    python nemlig_cli.py basket
    python nemlig_cli.py add PRODUCT_ID [--quantity N]
    python nemlig_cli.py history [ORDER_ID]

Credentials can be provided via ~/.config/nemlig/login.json or CLI options.
CLI options override the config file.
"""

import argparse
import itertools
import json
import os
import re
import readline
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

import argcomplete
import requests

# Optional: Anthropic for AI meal planning
try:
    import anthropic
    ANTHROPIC_AVAILABLE = True
except ImportError:
    ANTHROPIC_AVAILABLE = False

# Optional: Google Sheets for form responses
try:
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build
    GSHEETS_AVAILABLE = True
except ImportError:
    GSHEETS_AVAILABLE = False

GSHEETS_SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]

# Optional: Barcode scanning and image recognition
try:
    import cv2
    from pyzbar import pyzbar
    from PIL import Image
    import openfoodfacts
    SCANNER_AVAILABLE = True
except ImportError:
    SCANNER_AVAILABLE = False

# Optional: Raspberry Pi AI Camera
try:
    from picamera2 import Picamera2
    from picamera2.devices.imx500 import IMX500
    PICAMERA_AVAILABLE = True
except ImportError:
    PICAMERA_AVAILABLE = False


# Interactive mode commands for tab completion
COMMANDS = ["search", "details", "list", "basket", "help", "quit", "exit"]
LIST_SUBCOMMANDS = ["add", "remove", "clear", "budget", "sync"]


class NemligCompleter:
    """Tab completer for interactive mode."""

    def __init__(self):
        self.matches = []

    def complete(self, text: str, state: int) -> str | None:
        if state == 0:
            line = readline.get_line_buffer()
            self.matches = self._get_matches(line, text)
        return self.matches[state] if state < len(self.matches) else None

    def _get_matches(self, line: str, text: str) -> list[str]:
        parts = line.split()

        # First word - complete commands
        if not parts or (len(parts) == 1 and not line.endswith(" ")):
            return [cmd + " " for cmd in COMMANDS if cmd.startswith(text)]

        # After "list" - complete subcommands
        if parts[0] == "list":
            if len(parts) == 1 and line.endswith(" "):
                return [sub + " " for sub in LIST_SUBCOMMANDS]
            elif len(parts) == 2 and not line.endswith(" "):
                return [sub + " " for sub in LIST_SUBCOMMANDS if sub.startswith(text)]

        return []


class Spinner:
    """Animated spinner for long-running operations."""

    def __init__(self, message: str = "Loading"):
        self.message = message
        self.running = False
        self.thread = None
        self.frames = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

    def _spin(self):
        for frame in itertools.cycle(self.frames):
            if not self.running:
                break
            print(f"\r {frame} {self.message}...", end="", flush=True)
            time.sleep(0.08)

    def start(self):
        self.running = True
        self.thread = threading.Thread(target=self._spin)
        self.thread.start()

    def stop(self, final_message: str = None):
        self.running = False
        if self.thread:
            self.thread.join()
        # Clear the line
        print(f"\r{' ' * (len(self.message) + 10)}\r", end="")
        if final_message:
            print(f" ✓ {final_message}")

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *args):
        self.stop()


VERSION = "1.0.0"

LOGO = r"""
    ░░░    ░░░  ░░░░░░░  ░░░    ░░░  ░░░      ░░░   ░░░░░░░
    ░░░░  ░░░  ░░░      ░░░░  ░░░░  ░░░      ░░░  ░░░
    ░░░░░  ░░░  ░░░░░░  ░░░░░░░░░░  ░░░      ░░░  ░░░  ░░░░
    ░░░ ░░ ░░░  ░░░      ░░░ ░░ ░░░  ░░░      ░░░ ░░░   ░░░
    ░░░  ░░░░░  ░░░░░░░  ░░░    ░░░  ░░░░░░░  ░░░   ░░░░░░░

   ─────────────────────────────────────────────────────

       ██████╗ ██╗      ██╗   grocery shopping from your terminal
      ██╔════╝ ██║      ██║   ─────────────────────────────────
      ██║      ██║      ██║    search, list, sync - all from the cli
      ██║      ██║      ██║
       ██████╗ ███████╗ ██║    v{version}
       ╚═════╝ ╚══════╝ ╚═╝
"""


def print_welcome(username: str) -> None:
    """Print welcome banner with logo after login."""
    print(LOGO.format(version=VERSION))
    print(f"    Logged in as: {username}")
    print("   ─────────────────────────────────────────────────────\n")


def print_startup_logo() -> None:
    """Print startup logo before login."""
    print(LOGO.format(version=VERSION))
    print("   ─────────────────────────────────────────────────────\n")


CONFIG_FILE = Path.home() / ".config" / "nemlig" / "login.json"
ENV_FILE = Path.cwd() / ".env"


def load_env_file() -> None:
    """Load environment variables from .env file in current directory if it exists."""
    if ENV_FILE.exists():
        try:
            with open(ENV_FILE, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    # Skip empty lines and comments
                    if not line or line.startswith("#"):
                        continue
                    # Parse KEY=VALUE
                    if "=" in line:
                        key, value = line.split("=", 1)
                        key = key.strip()
                        value = value.strip()
                        # Remove quotes if present
                        if value.startswith('"') and value.endswith('"'):
                            value = value[1:-1]
                        elif value.startswith("'") and value.endswith("'"):
                            value = value[1:-1]
                        # Only set if not already in environment (CLI/existing env takes precedence)
                        if key not in os.environ:
                            os.environ[key] = value
        except Exception as e:
            print(f"Warning: Could not load .env file: {e}", file=sys.stderr)


def load_config_credentials() -> dict:
    """
    Load credentials from ~/.config/nemlig/login.json if it exists.

    Expected format: {"username": "email@example.com", "password": "secret"}

    Returns dict with 'username' and 'password' keys, or empty dict if file doesn't exist.

    Raises:
        ValueError: If file exists but contains invalid JSON or wrong structure.
        OSError: If file exists but cannot be read.
    """
    if not CONFIG_FILE.exists():
        return {}

    with open(CONFIG_FILE, encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, dict):
        raise ValueError(
            f"Config file {CONFIG_FILE} must contain a JSON object, got {type(data).__name__}"
        )

    return {
        "username": data.get("username"),
        "password": data.get("password"),
        "anthropic_api_key": data.get("anthropic_api_key"),
    }


def get_anthropic_api_key() -> str | None:
    """Get Anthropic API key from config file or environment."""
    # Try environment first
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if api_key:
        return api_key

    # Try config file
    try:
        creds = load_config_credentials()
        return creds.get("anthropic_api_key")
    except Exception:
        return None


# Google Sheets config
GSHEETS_CONFIG_FILE = Path.home() / ".config" / "nemlig" / "gsheets.json"
GSHEETS_TOKEN_FILE = Path.home() / ".config" / "nemlig" / "gsheets_token.json"
GSHEETS_CREDENTIALS_FILE = Path.home() / ".config" / "nemlig" / "credentials.json"


def load_gsheets_config() -> dict:
    """Load Google Sheets configuration."""
    if GSHEETS_CONFIG_FILE.exists():
        return json.loads(GSHEETS_CONFIG_FILE.read_text())
    return {}


def save_gsheets_config(config: dict) -> None:
    """Save Google Sheets configuration."""
    GSHEETS_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    GSHEETS_CONFIG_FILE.write_text(json.dumps(config, indent=2))


# Fridge inventory storage
FRIDGE_FILE = Path.home() / ".config" / "nemlig" / "fridge_inventory.json"


def load_fridge_inventory() -> dict:
    """Load fridge inventory from config file."""
    if FRIDGE_FILE.exists():
        return json.loads(FRIDGE_FILE.read_text())
    return {"items": [], "last_scan": None}


def save_fridge_inventory(data: dict) -> None:
    """Save fridge inventory to config file."""
    FRIDGE_FILE.parent.mkdir(parents=True, exist_ok=True)
    FRIDGE_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False))


# Grocery list storage
LIST_FILE = Path.home() / ".config" / "nemlig" / "grocery_list.json"


def load_grocery_list() -> dict:
    """Load grocery list from config file."""
    if LIST_FILE.exists():
        return json.loads(LIST_FILE.read_text())
    return {"budget": 500.0, "items": []}


def save_grocery_list(data: dict) -> None:
    """Save grocery list to config file."""
    LIST_FILE.parent.mkdir(parents=True, exist_ok=True)
    LIST_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False))


HISTORY_CACHE_FILE = Path.home() / ".config" / "nemlig" / "historik_cache.json"


def load_history_cache() -> dict | None:
    """Load compact order history cache. Returns None if missing."""
    if HISTORY_CACHE_FILE.exists():
        return json.loads(HISTORY_CACHE_FILE.read_text())
    return None


def save_history_cache(data: dict) -> None:
    """Save compact order history cache."""
    HISTORY_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    HISTORY_CACHE_FILE.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")))


BASE_URL = "https://www.nemlig.com"
SEARCH_API_URL = "https://webapi.prod.knl.nemlig.it/searchgateway/api"


@dataclass
class AuthTokens:
    """Authentication tokens for Nemlig API."""
    xsrf_token: str
    bearer_token: str
    session: requests.Session


class ProductNotFoundError(Exception):
    """Raised when a product cannot be found by ID."""
    pass


# Order status codes from the API
ORDER_STATUS_MAP = {
    1: "Pending",
    2: "Processing",
    4: "Delivered",
}

# Maximum orders to scan when looking up by ID
MAX_ORDER_HISTORY_LOOKUP = 100


def get_common_headers() -> dict:
    """Return common headers used for all API requests."""
    return {
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
        "Device-Size": "desktop",
        "Platform": "web",
        "Version": "11.201.0",
        "X-Correlation-Id": str(uuid.uuid4()),
    }


def login(username: str, password: str) -> AuthTokens:
    """
    Authenticate with Nemlig.com using the 3-step login flow.

    1. Get XSRF token
    2. Get Bearer token
    3. Login with credentials
    """
    session = requests.Session()
    headers = get_common_headers()

    spinner = Spinner("Connecting to nemlig.com")
    spinner.start()

    # Step 1: Get XSRF token
    resp = session.get(f"{BASE_URL}/webapi/AntiForgery", headers=headers)
    resp.raise_for_status()
    xsrf_data = resp.json()
    xsrf_token = xsrf_data["Value"]

    # Step 2: Get Bearer token
    headers["X-Correlation-Id"] = str(uuid.uuid4())
    resp = session.get(f"{BASE_URL}/webapi/Token", headers=headers)
    resp.raise_for_status()
    token_data = resp.json()
    bearer_token = token_data["access_token"]

    # Step 3: Login
    headers["X-Correlation-Id"] = str(uuid.uuid4())
    headers["X-XSRF-TOKEN"] = xsrf_token
    headers["Authorization"] = f"Bearer {bearer_token}"
    headers["Referer"] = f"{BASE_URL}/login?returnUrl=%2F"

    login_payload = {
        "Username": username,
        "Password": password,
        "CheckForExistingProducts": True,
        "DoMerge": True,
        "AppInstalled": False,
        "SaveExistingBasket": False,
    }

    resp = session.post(f"{BASE_URL}/webapi/login", headers=headers, json=login_payload)
    resp.raise_for_status()
    login_result = resp.json()

    if "RedirectUrl" not in login_result:
        raise Exception(f"Login failed: {login_result}")

    # Get fresh tokens after login
    headers["X-Correlation-Id"] = str(uuid.uuid4())
    resp = session.get(f"{BASE_URL}/webapi/Token", headers=headers)
    resp.raise_for_status()
    token_data = resp.json()
    bearer_token = token_data["access_token"]

    # Get fresh XSRF token
    resp = session.get(f"{BASE_URL}/webapi/AntiForgery", headers=headers)
    resp.raise_for_status()
    xsrf_data = resp.json()
    xsrf_token = xsrf_data["Value"]

    spinner.stop("Connected!")

    return AuthTokens(xsrf_token=xsrf_token, bearer_token=bearer_token, session=session)


def get_app_settings(auth: AuthTokens) -> dict:
    """Get app settings including timestamps needed for search."""
    headers = get_common_headers()
    headers["Authorization"] = f"Bearer {auth.bearer_token}"
    headers["X-XSRF-TOKEN"] = auth.xsrf_token

    resp = auth.session.get(f"{BASE_URL}/webapi/v2/AppSettings/Website", headers=headers)
    resp.raise_for_status()
    return resp.json()


def get_page_settings(auth: AuthTokens) -> dict:
    """Get page settings including timeslot info needed for search."""
    headers = get_common_headers()
    headers["Authorization"] = f"Bearer {auth.bearer_token}"
    headers["X-XSRF-TOKEN"] = auth.xsrf_token

    # First get app settings to get initial timestamp
    settings = get_app_settings(auth)
    timeslot_utc = "2025120216-180-1020"  # Default fallback

    # Get page JSON which contains timeslot info
    params = {"GetAsJson": "1", "d": "1"}
    resp = auth.session.get(f"{BASE_URL}/", headers=headers, params=params)
    resp.raise_for_status()
    data = resp.json()

    page_settings = data.get("Settings", {})
    if page_settings.get("TimeslotUtc"):
        timeslot_utc = page_settings["TimeslotUtc"]

    return {
        "timestamp": settings.get("CombinedProductsAndSitecoreTimestamp", ""),
        "timeslotUtc": timeslot_utc,
        "deliveryZoneId": page_settings.get("DeliveryZoneId", 1),
        "userId": page_settings.get("UserId", ""),
    }


def search_products(auth: AuthTokens, query: str, limit: int = 10) -> list:
    """
    Search for products on nemlig.com using the full search API.

    Returns a list of product dictionaries.
    """
    page_settings = get_page_settings(auth)

    headers = {
        "Accept": "application/json, text/plain, */*",
        "Authorization": f"Bearer {auth.bearer_token}",
        "X-Correlation-Id": str(uuid.uuid4()),
        "Referer": f"{BASE_URL}/",
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
    }

    params = {
        "query": query,
        "take": limit,
        "skip": 0,
        "recipeCount": 0,
        "timestamp": page_settings["timestamp"],
        "timeslotUtc": page_settings["timeslotUtc"],
        "deliveryZoneId": page_settings["deliveryZoneId"],
    }

    # Add user favorites if logged in
    if page_settings.get("userId"):
        params["includeFavorites"] = page_settings["userId"]

    resp = auth.session.get(f"{SEARCH_API_URL}/search", headers=headers, params=params)
    resp.raise_for_status()
    data = resp.json()

    # Full search returns products in Products.Products structure
    products_data = data.get("Products", {})
    products = products_data.get("Products", [])

    return products


def get_basket(auth: AuthTokens) -> dict:
    """Get the current shopping basket."""
    headers = get_common_headers()
    headers["Authorization"] = f"Bearer {auth.bearer_token}"
    headers["X-XSRF-TOKEN"] = auth.xsrf_token

    resp = auth.session.get(f"{BASE_URL}/webapi/basket/GetBasket", headers=headers)
    resp.raise_for_status()
    return resp.json()


def add_to_basket(auth: AuthTokens, product_id: str, quantity: int = 1) -> dict:
    """Add a product to the basket."""
    headers = get_common_headers()
    headers["Authorization"] = f"Bearer {auth.bearer_token}"
    headers["X-XSRF-TOKEN"] = auth.xsrf_token
    headers["Referer"] = f"{BASE_URL}/"

    payload = {
        "ProductId": product_id,
        "quantity": quantity,
        "AffectPartialQuantity": False,
        "disableQuantityValidation": False,
    }

    resp = auth.session.post(f"{BASE_URL}/webapi/basket/AddToBasket", headers=headers, json=payload)
    resp.raise_for_status()
    return resp.json()


def get_order_history(auth: AuthTokens, skip: int = 0, take: int = 10) -> dict:
    """Get paginated list of past orders."""
    headers = get_common_headers()
    headers["Authorization"] = f"Bearer {auth.bearer_token}"
    headers["X-XSRF-TOKEN"] = auth.xsrf_token

    params = {"skip": skip, "take": take}
    resp = auth.session.get(f"{BASE_URL}/webapi/order/GetBasicOrderHistory", headers=headers, params=params)
    resp.raise_for_status()
    return resp.json()


def get_order_details(auth: AuthTokens, order_id: int) -> dict:
    """Get detailed line items for a specific order."""
    headers = get_common_headers()
    headers["Authorization"] = f"Bearer {auth.bearer_token}"
    headers["X-XSRF-TOKEN"] = auth.xsrf_token

    resp = auth.session.get(f"{BASE_URL}/webapi/v2/order/GetOrderHistory/{order_id}", headers=headers)
    resp.raise_for_status()
    return resp.json()


def get_product_details(auth: AuthTokens, product_id: str) -> dict:
    """
    Get detailed product information using the GetAsJson endpoint.

    First searches for the product to get its URL, then fetches the full details.

    Raises:
        ProductNotFoundError: If product_id is not found or details unavailable.
    """
    # First, search to get the product URL (required because URL contains product name slug)
    products = search_products(auth, product_id, limit=5)

    # Find the exact product by ID
    product_url = None
    for p in products:
        if p.get("Id") == product_id:
            product_url = p.get("Url")
            break

    if not product_url:
        raise ProductNotFoundError(
            f"Product {product_id} not found. Search returned {len(products)} products but none matched ID."
        )

    # Get page settings for timeslot
    page_settings = get_page_settings(auth)

    headers = get_common_headers()
    headers["Authorization"] = f"Bearer {auth.bearer_token}"
    headers["X-XSRF-TOKEN"] = auth.xsrf_token

    params = {
        "GetAsJson": "1",
        "t": page_settings["timeslotUtc"],
        "d": "1",
    }

    resp = auth.session.get(f"{BASE_URL}/{product_url}", headers=headers, params=params)
    resp.raise_for_status()
    data = resp.json()

    # Extract product details from content array
    content = data.get("content", [])
    for item in content:
        if item.get("TemplateName") == "productdetailspot":
            return item

    template_names = [item.get("TemplateName", "unknown") for item in content]
    raise ProductNotFoundError(
        f"Product {product_id}: No 'productdetailspot' in response. Found templates: {template_names}"
    )


def strip_html_tags(html: str) -> str:
    """Remove HTML tags from text, returning plain text."""
    return re.sub(r"<[^>]+>", "", html).strip()


def wrap_text(text: str, width: int = 80, indent: str = "  ") -> list[str]:
    """Wrap text to specified width with indentation."""
    lines = []
    words = text.split()
    current_line = indent

    for word in words:
        if len(current_line) + len(word) + 1 > width:
            lines.append(current_line)
            current_line = indent + word
        else:
            if current_line == indent:
                current_line += word
            else:
                current_line += " " + word

    if current_line.strip():
        lines.append(current_line)

    return lines


def format_product(product: dict) -> str:
    """Format a product for display."""
    price = product.get("Price", 0)
    name = product.get("Name", "Unknown")
    brand = product.get("Brand", "")
    description = product.get("Description", "")
    product_id = product.get("Id", "")
    image_url = product.get("PrimaryImage", "")
    available = product.get("Availability", {}).get("IsAvailableInStock", False)

    availability_str = "In stock" if available else "OUT OF STOCK"

    line = f"  [{product_id}] {name} ({brand}) - {price:.2f} kr - {description} [{availability_str}]"
    if image_url:
        line += f"\n    Image: {image_url}"
    return line


def format_basket_line(line: dict) -> str:
    """Format a basket line item for display."""
    name = line.get("Name", "Unknown")
    brand = line.get("Brand", "")
    quantity = line.get("Quantity", 0)
    item_price = line.get("ItemPrice", 0)
    total_price = line.get("Price", 0)
    product_id = line.get("Id", "")

    return f"  [{product_id}] {name} ({brand}) x{quantity} @ {item_price:.2f} kr = {total_price:.2f} kr"


def format_order_summary(order: dict) -> str:
    """Format an order for the history list view."""
    order_num = order.get("OrderNumber", "Unknown")
    order_id = order.get("Id", "")
    total = order.get("Total", 0)
    status_code = order.get("Status", 0)
    order_date = order.get("OrderDate", "")

    # Parse date for display (ISO format: 2025-11-25T06:07:18Z)
    if order_date:
        date_part = order_date.split("T")[0]
    else:
        date_part = "Unknown"

    status = ORDER_STATUS_MAP.get(status_code, f"Status {status_code}")

    # Delivery time window
    delivery_time = order.get("DeliveryTime", {})
    delivery_start = delivery_time.get("Start", "")
    delivery_end = delivery_time.get("End", "")
    if delivery_start and delivery_end:
        # Extract time part (HH:MM)
        start_time = delivery_start.split("T")[1][:5] if "T" in delivery_start else ""
        end_time = delivery_end.split("T")[1][:5] if "T" in delivery_end else ""
        delivery_date = delivery_start.split("T")[0] if "T" in delivery_start else ""
        delivery_str = f"{delivery_date} {start_time}-{end_time}"
    else:
        delivery_str = "N/A"

    return f"  [{order_id}] {order_num} - {date_part} - {total:.2f} kr - {status} - Delivery: {delivery_str}"


def format_order_line(line: dict) -> str:
    """Format an order line item for display."""
    name = line.get("ProductName", "Unknown")
    quantity = line.get("Quantity", 0)
    amount = line.get("Amount", 0)
    avg_price = line.get("AverageItemPrice", 0)
    product_num = line.get("ProductNumber", "")
    description = line.get("Description", "")
    has_campaign = line.get("HasCampaign", False)

    campaign_str = " [OFFER]" if has_campaign else ""
    return f"  [{product_num}] {name} - {description} x{quantity:.0f} @ {avg_price:.2f} kr = {amount:.2f} kr{campaign_str}"


def format_order_details(order: dict, lines: list) -> str:
    """Format full order details with line items."""
    output = []

    order_num = order.get("OrderNumber", "Unknown")
    order_id = order.get("Id", "")
    total = order.get("Total", 0)
    subtotal = order.get("SubTotal", 0)
    delivery_fee = total - subtotal

    output.append(f"Order {order_num}")
    output.append("=" * (len(f"Order {order_num}")))
    output.append("")
    output.append(f"Order ID:     {order_id}")
    output.append(f"Subtotal:     {subtotal:.2f} kr")
    output.append(f"Delivery:     {delivery_fee:.2f} kr")
    output.append(f"Total:        {total:.2f} kr")
    output.append("")
    output.append(f"Items ({len(lines)}):")

    for line in lines:
        output.append(format_order_line(line))

    # Calculate totals from lines
    line_total = sum(line.get("Amount", 0) for line in lines)
    output.append("")
    output.append(f"  Lines total: {line_total:.2f} kr")

    return "\n".join(output)


def format_product_details(product: dict) -> str:
    """Format detailed product information for display."""
    lines = []

    # Basic info
    name = product.get("Name", "Unknown")
    brand = product.get("Brand", "")
    product_id = product.get("Id", "")
    price = product.get("Price", 0)
    unit_price = product.get("UnitPriceCalc", 0)
    unit_label = product.get("UnitPriceLabel", "")
    description = product.get("Description", "")
    category = product.get("Category", "")
    subcategory = product.get("SubCategory", "")

    lines.append(f"{name}")
    lines.append(f"{'=' * len(name)}")
    lines.append("")
    lines.append(f"ID:          {product_id}")
    lines.append(f"Brand:       {brand}")
    lines.append(f"Category:    {category} > {subcategory}")
    lines.append(f"Description: {description}")
    lines.append("")
    lines.append(f"Price:       {price:.2f} kr ({unit_price:.2f} {unit_label})")

    # Campaign info
    campaign = product.get("Campaign")
    if campaign:
        campaign_type = campaign.get("Type", "")
        min_qty = campaign.get("MinQuantity", 0)
        campaign_price = campaign.get("TotalPrice", 0)
        lines.append(f"Campaign:    {min_qty} for {campaign_price:.2f} kr ({campaign_type})")

    # Availability
    availability = product.get("Availability", {})
    in_stock = availability.get("IsAvailableInStock", False)
    delivery_ok = availability.get("IsDeliveryAvailable", False)
    stock_status = "In stock" if in_stock else "OUT OF STOCK"
    delivery_status = "Available" if delivery_ok else "Not available"
    lines.append("")
    lines.append(f"Stock:       {stock_status}")
    lines.append(f"Delivery:    {delivery_status}")

    # Attributes
    attributes = product.get("Attributes", [])
    if attributes:
        lines.append("")
        lines.append("Attributes:")
        for attr in attributes:
            attr_name = attr.get("Name", "")
            attr_value = attr.get("Value", "")
            lines.append(f"  {attr_name}: {attr_value}")

    # Labels
    labels = product.get("Labels", [])
    if labels:
        lines.append("")
        lines.append(f"Labels:      {', '.join(labels)}")

    # Product description (HTML text, strip tags for CLI)
    text = product.get("Text", "")
    if text:
        clean_text = strip_html_tags(text)
        if clean_text:
            lines.append("")
            lines.append("About:")
            lines.extend(wrap_text(clean_text))

    # URL
    url = product.get("Url", "")
    if url:
        lines.append("")
        lines.append(f"URL:         {BASE_URL}/{url}")

    return "\n".join(lines)


def cmd_search(auth: AuthTokens, args: argparse.Namespace) -> int:
    """Handle the search command."""
    query = args.query
    limit = args.limit

    print(f"Searching for '{query}'...", file=sys.stderr)
    products = search_products(auth, query, limit)

    if not products:
        print(f"No products found for '{query}'")
        return 1

    print(f"\nFound {len(products)} products:\n")
    for product in products:
        print(format_product(product))

    return 0


def cmd_basket(auth: AuthTokens, args: argparse.Namespace) -> int:
    """Handle the basket command."""
    print("Fetching basket...", file=sys.stderr)
    basket = get_basket(auth)

    lines = basket.get("Lines", [])

    if not lines:
        print("Your basket is empty.")
        return 0

    print(f"\nBasket ({len(lines)} items):\n")

    total = 0
    for line in lines:
        print(format_basket_line(line))
        total += line.get("Price", 0)

    print(f"\n  Total: {total:.2f} kr")

    return 0


def cmd_add(auth: AuthTokens, args: argparse.Namespace) -> int:
    """Handle the add command."""
    product_id = args.product_id
    quantity = args.quantity

    print(f"Adding product {product_id} (quantity: {quantity}) to basket...", file=sys.stderr)

    result = add_to_basket(auth, product_id, quantity)

    # Find the added product in the result
    lines = result.get("Lines", [])
    added_line = None
    for line in lines:
        if line.get("Id") == product_id:
            added_line = line
            break

    if added_line:
        print("\nAdded to basket:")
        print(format_basket_line(added_line))
    else:
        print(f"Product {product_id} added to basket.")

    # Show basket total
    total = sum(line.get("Price", 0) for line in lines)
    print(f"\nBasket total: {total:.2f} kr ({len(lines)} items)")

    return 0


def cmd_details(auth: AuthTokens, args: argparse.Namespace) -> int:
    """Handle the details command."""
    product_id = args.product_id

    print(f"Fetching details for product {product_id}...", file=sys.stderr)

    try:
        product = get_product_details(auth, product_id)
    except ProductNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    print()
    print(format_product_details(product))

    return 0


def cmd_history(auth: AuthTokens, args: argparse.Namespace) -> int:
    """Handle the history command."""
    order_id = args.order_id
    limit = args.limit

    if order_id:
        # Show details for specific order
        print(f"Fetching order {order_id}...", file=sys.stderr)

        # Get order summary from recent history
        history = get_order_history(auth, skip=0, take=MAX_ORDER_HISTORY_LOOKUP)
        orders = history.get("Orders", [])
        order = None
        for o in orders:
            if o.get("Id") == order_id:
                order = o
                break

        if not order:
            print(f"Order {order_id} not found in last {MAX_ORDER_HISTORY_LOOKUP} orders.", file=sys.stderr)
            return 1

        # Get line items
        details = get_order_details(auth, order_id)
        lines = details.get("Lines", [])

        print()
        print(format_order_details(order, lines))
    else:
        # List recent orders
        print("Fetching order history...", file=sys.stderr)
        history = get_order_history(auth, skip=0, take=limit)
        orders = history.get("Orders", [])
        num_pages = history.get("NumberOfPages", 1)

        if not orders:
            print("No orders found.")
            return 0

        print(f"\nOrder History ({len(orders)} orders, {num_pages} pages total):\n")
        for order in orders:
            print(format_order_summary(order))

        print("\nUse 'history ORDER_ID' to see order details.")

    return 0


def cmd_refresh_history(auth: AuthTokens, args: argparse.Namespace) -> int:
    """Fetch order history with line items and write a compact cache file."""
    from datetime import datetime, timezone

    limit = args.limit
    print(f"Fetching last {limit} orders...", file=sys.stderr)
    history = get_order_history(auth, skip=0, take=limit)
    orders = history.get("Orders", [])

    if not orders:
        print("No orders found.", file=sys.stderr)
        return 1

    cached_orders = []
    for i, order in enumerate(orders, start=1):
        order_id = order.get("Id")
        print(f"  [{i}/{len(orders)}] Order {order_id}...", file=sys.stderr)
        details = get_order_details(auth, order_id)
        lines = details.get("Lines", [])
        cached_orders.append({
            "id": order_id,
            "date": order.get("DeliveryDate") or order.get("OrderDate"),
            "items": [
                {
                    "pid": line.get("ProductNumber"),
                    "name": line.get("ProductName"),
                    "qty": line.get("Quantity", 0),
                }
                for line in lines
            ],
        })

    data = {
        "refreshed_at": datetime.now(timezone.utc).isoformat(),
        "orders": cached_orders,
    }
    save_history_cache(data)

    total_items = sum(len(o["items"]) for o in cached_orders)
    print(
        f"Cached {len(cached_orders)} orders ({total_items} line items) to {HISTORY_CACHE_FILE}",
        file=sys.stderr,
    )
    return 0


def cmd_list_show(args: argparse.Namespace) -> int:
    """Display the current grocery list."""
    data = load_grocery_list()
    # Simple display for now
    if not data["items"]:
        print("Your grocery list is empty.")
        print(f"Budget: {data['budget']:.2f} kr")
    else:
        print(f"Grocery List ({len(data['items'])} items):")
        total = 0
        for item in data["items"]:
            subtotal = item["unit_price"] * item["quantity"]
            total += subtotal
            print(f"  [{item['product_id']}] {item['name']} x{item['quantity']} @ {item['unit_price']:.2f} kr = {subtotal:.2f} kr")
        print(f"\n  Total: {total:.2f} kr / Budget: {data['budget']:.2f} kr")
        remaining = data['budget'] - total
        if remaining < 0:
            print(f"  ⚠ Over budget by {-remaining:.2f} kr!")
        else:
            print(f"  Remaining: {remaining:.2f} kr")
    return 0


def cmd_list_add(auth: AuthTokens, args: argparse.Namespace) -> int:
    """Add a product to the grocery list."""
    product_id = args.product_id
    quantity = args.quantity

    # If not a numeric ID, treat as search query
    if not product_id.isdigit():
        print(f"Searching for '{product_id}'...", file=sys.stderr)
        products = search_products(auth, product_id, limit=10)

        if not products:
            print(f"No products found for '{product_id}'")
            return 1

        print(f"\nFound {len(products)} products:\n")
        for i, p in enumerate(products, 1):
            price = p.get("Price", 0)
            name = p.get("Name", "Unknown")
            brand = p.get("Brand", "")
            available = p.get("Availability", {}).get("IsAvailableInStock", False)
            stock = "In stock" if available else "OUT OF STOCK"
            print(f"  [{i}] {name} ({brand}) - {price:.2f} kr [{stock}]")

        print()
        try:
            choice = input("Enter number to add (or 'q' to cancel): ").strip()
            if choice.lower() == 'q':
                print("Cancelled.")
                return 0
            idx = int(choice) - 1
            if idx < 0 or idx >= len(products):
                print("Invalid selection.", file=sys.stderr)
                return 1
            product = products[idx]
            product_id = product.get("Id")
        except (ValueError, EOFError, KeyboardInterrupt):
            print("\nCancelled.")
            return 0
    else:
        print(f"Fetching product {product_id}...", file=sys.stderr)
        try:
            product = get_product_details(auth, product_id)
        except ProductNotFoundError as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1

    data = load_grocery_list()

    # Check if product already in list
    for item in data["items"]:
        if item["product_id"] == product_id:
            item["quantity"] += quantity
            item["unit_price"] = product.get("Price", item["unit_price"])
            save_grocery_list(data)
            print(f"Updated quantity: {item['name']} x{item['quantity']}")
            return cmd_list_show(args)

    # Add new item
    new_item = {
        "product_id": product_id,
        "name": product.get("Name", "Unknown"),
        "brand": product.get("Brand", ""),
        "quantity": quantity,
        "unit_price": product.get("Price", 0),
    }
    data["items"].append(new_item)
    save_grocery_list(data)

    print(f"Added: {new_item['name']} x{quantity}")
    return cmd_list_show(args)


def cmd_list_remove(args: argparse.Namespace) -> int:
    """Remove a product from the grocery list."""
    product_id = args.product_id

    data = load_grocery_list()

    for i, item in enumerate(data["items"]):
        if item["product_id"] == product_id:
            removed = data["items"].pop(i)
            save_grocery_list(data)
            print(f"Removed: {removed['name']}")
            return cmd_list_show(args)

    print(f"Product {product_id} not found in list.", file=sys.stderr)
    return 1


def cmd_list_clear(args: argparse.Namespace) -> int:
    """Clear all items from the grocery list."""
    data = load_grocery_list()
    count = len(data["items"])
    data["items"] = []
    save_grocery_list(data)
    print(f"Cleared {count} items from list.")
    return 0


def cmd_list_budget(args: argparse.Namespace) -> int:
    """Show or set the budget."""
    data = load_grocery_list()

    if args.amount is not None:
        data["budget"] = args.amount
        save_grocery_list(data)
        print(f"Budget set to {args.amount:.2f} kr")
    else:
        budget = data["budget"]
        total = sum(item.get("unit_price", 0) * item.get("quantity", 1) for item in data["items"])
        remaining = budget - total
        print(f"Current budget: {budget:.2f} kr")
        print(f"List total:     {total:.2f} kr")
        print(f"Remaining:      {remaining:.2f} kr")

    return 0


def cmd_list_sync(auth: AuthTokens, args: argparse.Namespace) -> int:
    """Sync grocery list to nemlig basket."""
    data = load_grocery_list()

    if not data["items"]:
        print("Grocery list is empty. Nothing to sync.")
        return 0

    print(f"Syncing {len(data['items'])} items to basket...", file=sys.stderr)

    success_count = 0
    for item in data["items"]:
        product_id = item["product_id"]
        quantity = item["quantity"]
        try:
            add_to_basket(auth, product_id, quantity)
            print(f"  ✓ {item['name']} x{quantity}")
            success_count += 1
        except Exception as e:
            print(f"  ✗ {item['name']} - Error: {e}", file=sys.stderr)

    print(f"\nSynced {success_count}/{len(data['items'])} items to basket.")

    if success_count > 0:
        print("\nUse 'basket' command to view your nemlig basket.")

    return 0 if success_count == len(data["items"]) else 1


def interactive_mode(auth: AuthTokens, username: str) -> int:
    """Run interactive REPL mode."""
    print_welcome(username)

    # Set up tab completion
    completer = NemligCompleter()
    readline.set_completer(completer.complete)
    readline.set_completer_delims(" ")

    # macOS uses libedit which needs different binding syntax
    if readline.__doc__ and "libedit" in readline.__doc__:
        readline.parse_and_bind("bind ^I rl_complete")
    else:
        readline.parse_and_bind("tab: complete")

    # Show quick help
    print("    Commands: search <query> | list | basket | help | quit\n")

    while True:
        try:
            cmd = input("  nemlig> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n\n    Goodbye! 👋\n")
            return 0

        if not cmd:
            continue

        parts = cmd.split()
        command = parts[0].lower()

        if command in ("quit", "exit", "q"):
            print("\n    Goodbye! 👋\n")
            return 0

        elif command == "help":
            print("""
    Available commands:
    ─────────────────────────────────────────────────────
    search <query>      Search for products
    details <id>        Show product details
    list                Show your grocery list
    list add <query>    Add product to list (search by name)
    list remove <id>    Remove product from list
    list clear          Clear grocery list
    list budget [amt]   Show/set budget
    list sync           Push list to nemlig basket
    basket              Show nemlig basket
    help                Show this help
    quit                Exit
    ─────────────────────────────────────────────────────
""")

        elif command == "search" and len(parts) > 1:
            query = " ".join(parts[1:])
            spinner = Spinner(f"Searching for '{query}'")
            spinner.start()
            products = search_products(auth, query, limit=10)
            if not products:
                spinner.stop(f"No products found for '{query}'")
            else:
                spinner.stop(f"Found {len(products)} products")
                print()
                for p in products:
                    price = p.get("Price", 0)
                    name = p.get("Name", "Unknown")
                    brand = p.get("Brand", "")
                    pid = p.get("Id", "")
                    available = p.get("Availability", {}).get("IsAvailableInStock", False)
                    stock = "In stock" if available else "OUT OF STOCK"
                    print(f"  [{pid}] {name} ({brand}) - {price:.2f} kr [{stock}]")

                print()

        elif command == "details" and len(parts) > 1:
            product_id = parts[1]
            spinner = Spinner(f"Loading product {product_id}")
            spinner.start()
            try:
                product = get_product_details(auth, product_id)
                spinner.stop("Product loaded")
                print()
                print(format_product_details(product))
                print()
            except ProductNotFoundError as e:
                spinner.stop()
                print(f"Error: {e}\n")

        elif command == "list":
            if len(parts) == 1:
                cmd_list_show(args)
                print()
            elif parts[1] == "add" and len(parts) > 2:
                query = " ".join(parts[2:])
                spinner = Spinner(f"Searching for '{query}'")
                spinner.start()
                products = search_products(auth, query, limit=10)
                if not products:
                    spinner.stop(f"No products found for '{query}'")
                else:
                    spinner.stop(f"Found {len(products)} products")
                    print()
                    for i, p in enumerate(products, 1):
                        price = p.get("Price", 0)
                        name = p.get("Name", "Unknown")
                        brand = p.get("Brand", "")
                        available = p.get("Availability", {}).get("IsAvailableInStock", False)
                        stock = "In stock" if available else "OUT OF STOCK"
                        print(f"  [{i}] {name} ({brand}) - {price:.2f} kr [{stock}]")
                    print()
                    try:
                        choice = input("  Enter number to add (or 'q' to cancel): ").strip()
                        if choice.lower() != 'q':
                            idx = int(choice) - 1
                            if 0 <= idx < len(products):
                                product = products[idx]
                                data = load_grocery_list()
                                product_id = product.get("Id")

                                # Check if already in list
                                found = False
                                for item in data["items"]:
                                    if item["product_id"] == product_id:
                                        item["quantity"] += 1
                                        item["unit_price"] = product.get("Price", item["unit_price"])
                                        found = True
                                        break

                                if not found:
                                    data["items"].append({
                                        "product_id": product_id,
                                        "name": product.get("Name", "Unknown"),
                                        "brand": product.get("Brand", ""),
                                        "quantity": 1,
                                        "unit_price": product.get("Price", 0),
                                    })
                                save_grocery_list(data)
                                print(f"\n  ✓ Added: {product.get('Name')}")
                                cmd_list_show(args)
                    except (ValueError, KeyboardInterrupt):
                        print("Cancelled.")
                    print()
            elif parts[1] == "remove" and len(parts) > 2:
                product_id = parts[2]
                data = load_grocery_list()
                for i, item in enumerate(data["items"]):
                    if item["product_id"] == product_id:
                        removed = data["items"].pop(i)
                        save_grocery_list(data)
                        print(f"  ✓ Removed: {removed['name']}\n")
                        break
                else:
                    print(f"  Product {product_id} not in list\n")
            elif parts[1] == "clear":
                data = load_grocery_list()
                count = len(data["items"])
                data["items"] = []
                save_grocery_list(data)
                print(f"  ✓ Cleared {count} items\n")
            elif parts[1] == "budget":
                data = load_grocery_list()
                if len(parts) > 2:
                    try:
                        data["budget"] = float(parts[2])
                        save_grocery_list(data)
                        print(f"  ✓ Budget set to {data['budget']:.2f} kr\n")
                    except ValueError:
                        print("  Invalid amount\n")
                else:
                    total = sum(item.get("unit_price", 0) * item.get("quantity", 1) for item in data["items"])
                    budget = data["budget"]
                    remaining = budget - total
                    print(f"  Budget: {budget:.2f} kr | Used: {total:.2f} kr | Remaining: {remaining:.2f} kr\n")
            elif parts[1] == "sync":
                data = load_grocery_list()
                if not data["items"]:
                    print("  List is empty\n")
                else:
                    print(f"  Syncing {len(data['items'])} items...")
                    for item in data["items"]:
                        try:
                            add_to_basket(auth, item["product_id"], item["quantity"])
                            print(f"    ✓ {item['name']} x{item['quantity']}")
                        except Exception as e:
                            print(f"    ✗ {item['name']} - {e}")
                    print("  Done! Use 'basket' to view.\n")
            else:
                print("  Usage: list | list add <query> | list remove <id> | list clear | list budget [amt] | list sync\n")

        elif command == "basket":
            spinner = Spinner("Loading basket")
            spinner.start()
            basket = get_basket(auth)
            spinner.stop("Basket loaded")
            lines = basket.get("Lines", [])
            if not lines:
                print("  Basket is empty\n")
            else:
                print(f"\n  Basket ({len(lines)} items):\n")
                total = 0
                for line in lines:
                    print(f"  {format_basket_line(line)}")
                    total += line.get("Price", 0)
                print(f"\n  Total: {total:.2f} kr\n")

        else:
            print("  Unknown command. Type 'help' for available commands.\n")


def main():
    # Load .env file if present (before parsing credentials)
    load_env_file()

    parser = argparse.ArgumentParser(
        description=LOGO.format(version=VERSION),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Credentials:
  Credentials are loaded from .env, ~/.config/nemlig/login.json, or CLI options (-u, -p).
  .env file in current directory takes lowest priority (CLI/config override it).
  CLI options (-u, -p) override both .env and config file values.

  Config file format:
    {"username": "email@example.com", "password": "secret"}

Examples:
  %(prog)s search "cocio"
  %(prog)s list add "mælk"
  %(prog)s list
  %(prog)s list sync
  %(prog)s basket
        """
    )

    parser.add_argument("-v", "--version", action="version", version=f"%(prog)s {VERSION}")
    parser.add_argument("-u", "--username", help="Nemlig.com email/username (overrides config file)")
    parser.add_argument("-p", "--password", help="Nemlig.com password (overrides config file)")

    subparsers = parser.add_subparsers(dest="command", required=False)

    # Search command
    search_parser = subparsers.add_parser("search", help="Search for products")
    search_parser.add_argument("query", help="Search query")
    search_parser.add_argument("-l", "--limit", type=int, default=10, help="Max results (default: 10)")

    # Details command
    details_parser = subparsers.add_parser("details", help="Show detailed product info")
    details_parser.add_argument("product_id", help="Product ID to view")

    # Basket command
    subparsers.add_parser("basket", help="Show current basket")

    # Add command
    add_parser = subparsers.add_parser("add", help="Add product to basket")
    add_parser.add_argument("product_id", help="Product ID to add")
    add_parser.add_argument("-q", "--quantity", type=int, default=1, help="Quantity (default: 1)")

    # History command
    history_parser = subparsers.add_parser("history", help="Show order history")
    history_parser.add_argument("order_id", nargs="?", type=int, help="Order ID for details (optional)")
    history_parser.add_argument("-l", "--limit", type=int, default=10, help="Max orders to show (default: 10)")

    # Refresh-history command — cache compact order history locally
    refresh_history_parser = subparsers.add_parser(
        "refresh-history",
        help="Fetch and cache compact order history (with line items) for preference lookup",
    )
    refresh_history_parser.add_argument(
        "-l", "--limit", type=int, default=20,
        help="Number of recent orders to cache (default: 20)",
    )

    # List command with subcommands
    list_parser = subparsers.add_parser("list", help="Manage grocery list")
    list_sub = list_parser.add_subparsers(dest="list_cmd")

    # list (show) - default when no subcommand
    list_sub.add_parser("show", help="Show current grocery list")

    # list add
    list_add_parser = list_sub.add_parser("add", help="Add product to list")
    list_add_parser.add_argument("product_id", help="Product ID or search term")
    list_add_parser.add_argument("-q", "--quantity", type=int, default=1, help="Quantity (default: 1)")

    # list remove
    list_remove_parser = list_sub.add_parser("remove", help="Remove product from list")
    list_remove_parser.add_argument("product_id", help="Product ID to remove")

    # list clear
    list_sub.add_parser("clear", help="Clear all items from list")

    # list budget
    list_budget_parser = list_sub.add_parser("budget", help="Show or set budget")
    list_budget_parser.add_argument("amount", nargs="?", type=float, help="New budget amount in kr")

    # list sync
    list_sub.add_parser("sync", help="Push list items to nemlig basket")

    argcomplete.autocomplete(parser)
    args = parser.parse_args()

    # Handle list commands that don't require authentication
    if args.command == "list":
        list_cmd = args.list_cmd
        # Commands that don't need auth
        if list_cmd is None or list_cmd == "show":
            return cmd_list_show(args)
        elif list_cmd == "remove":
            return cmd_list_remove(args)
        elif list_cmd == "clear":
            return cmd_list_clear(args)
        elif list_cmd == "budget":
            return cmd_list_budget(args)
        # Commands that need auth fall through to below

    # Load credentials: config file first, CLI overrides
    try:
        config_creds = load_config_credentials()
    except (json.JSONDecodeError, ValueError, OSError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    username = args.username or config_creds.get("username") or os.environ.get("NEMLIG_USER")
    password = args.password or config_creds.get("password") or os.environ.get("NEMLIG_PASS")

    if not username or not password:
        missing = []
        if not username:
            missing.append("username")
        if not password:
            missing.append("password")

        if CONFIG_FILE.exists() and config_creds:
            hint = f"Config file {CONFIG_FILE} missing {', '.join(missing)}."
        elif CONFIG_FILE.exists():
            hint = f"Config file {CONFIG_FILE} failed to load."
        else:
            hint = f"No config file at {CONFIG_FILE}."

        print(
            f"Error: Missing {' and '.join(missing)}. {hint} "
            f"Provide via config file or -u/-p options.",
            file=sys.stderr,
        )
        return 1

    try:
        # Authenticate
        auth = login(username, password)

        # Interactive mode if no command given
        if args.command is None:
            return interactive_mode(auth, username)

        # Single command mode - show welcome banner
        print_welcome(username)

        # Execute command
        if args.command == "search":
            return cmd_search(auth, args)
        elif args.command == "details":
            return cmd_details(auth, args)
        elif args.command == "basket":
            return cmd_basket(auth, args)
        elif args.command == "add":
            return cmd_add(auth, args)
        elif args.command == "history":
            return cmd_history(auth, args)
        elif args.command == "refresh-history":
            return cmd_refresh_history(auth, args)
        elif args.command == "list":
            # List commands that require auth
            if args.list_cmd == "add":
                return cmd_list_add(auth, args)
            elif args.list_cmd == "sync":
                return cmd_list_sync(auth, args)
        else:
            print(f"Unknown command: {args.command}", file=sys.stderr)
            return 1

    except requests.exceptions.HTTPError as e:
        print(f"HTTP Error: {e}", file=sys.stderr)
        if e.response is not None:
            print(f"Response: {e.response.text}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
