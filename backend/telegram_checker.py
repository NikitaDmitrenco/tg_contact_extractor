import os
import re
import asyncio
import logging
from typing import List, Dict, Any, Optional, Tuple
from pathlib import Path
from dotenv import load_dotenv

from telethon import TelegramClient
from telethon.tl.functions.contacts import ImportContactsRequest, DeleteContactsRequest
from telethon.tl.types import InputPhoneContact, User
from telethon.errors import FloodWaitError, ApiIdInvalidError, AuthKeyInvalidError, UserDeactivatedError, SessionPasswordNeededError

# Setup logging
logger = logging.getLogger("telegram_checker")
logger.setLevel(logging.INFO)
if not logger.handlers:
    ch = logging.StreamHandler()
    formatter = logging.Formatter("[%(asctime)s] %(levelname)s - %(message)s")
    ch.setFormatter(formatter)
    logger.addHandler(ch)

# Base directory setup
BASE_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = BASE_DIR / ".env"

if os.getenv("VERCEL"):
    SESSIONS_DIR = Path("/tmp/sessions")
else:
    SESSIONS_DIR = BASE_DIR / "backend" / "sessions"

try:
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
except Exception as e:
    logger.warning(f"Could not create sessions dir at {SESSIONS_DIR}: {e}")
    SESSIONS_DIR = Path("/tmp/sessions")
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)

SESSION_FILE = SESSIONS_DIR / "tg_checker_session"

class ConfigError(Exception):
    """Raised when .env configuration is missing or invalid."""
    pass

class CriticalTelegramError(Exception):
    """Raised when Telegram connection or authorization fails critically."""
    pass

def load_credentials() -> Tuple[int, str]:
    """
    Loads and validates API_ID and API_HASH from environment variables or .env.
    Never logs full credentials.
    """
    if ENV_PATH.exists():
        load_dotenv(dotenv_path=ENV_PATH, override=True)
    else:
        load_dotenv(override=True)
    
    api_id_str = os.getenv("API_ID", "").strip()
    api_hash = os.getenv("API_HASH", "").strip()

    if not api_id_str or api_id_str == "YOUR_API_ID":
        raise ConfigError(
            "Configuration error.\n\nPlease open .env and fill in:\nAPI_ID=...\nAPI_HASH=..."
        )

    if not api_hash or api_hash == "YOUR_API_HASH":
        raise ConfigError(
            "Configuration error.\n\nPlease open .env and fill in:\nAPI_ID=...\nAPI_HASH=..."
        )

    try:
        api_id = int(api_id_str)
    except ValueError:
        raise ConfigError(
            "Configuration error: API_ID must be a numeric integer.\nCheck your .env file."
        )

    masked_hash = api_hash[:4] + "..." + api_hash[-4:] if len(api_hash) > 8 else "***"
    logger.info(f"Loaded Telegram Credentials: API_ID={api_id}, API_HASH={masked_hash}")
    return api_id, api_hash

import json

def parse_phone_numbers(raw_content: str) -> List[str]:
    """
    Parses phone numbers from raw text, CSV, or JSON file content.
    Supports:
    1. Plain text / CSV separated by ';', ',', or newlines.
    2. JSON arrays: ["+37369123456", "+37368123456"]
    3. JSON objects or lists of dicts: [{"phone": "+37369123456"}]
    4. Strips surrounding whitespace and quotes.
    5. Deduplicates while preserving order.
    """
    parsed_candidates = []

    # 1. Attempt JSON parsing
    try:
        data = json.loads(raw_content)
        if isinstance(data, list):
            for item in data:
                if isinstance(item, str):
                    parsed_candidates.append(item)
                elif isinstance(item, dict):
                    for val in item.values():
                        if isinstance(val, (str, int)):
                            parsed_candidates.append(str(val))
        elif isinstance(data, dict):
            for val in data.values():
                if isinstance(val, str):
                    parsed_candidates.append(val)
                elif isinstance(val, list):
                    for elem in val:
                        if isinstance(elem, (str, int)):
                            parsed_candidates.append(str(elem))
    except Exception:
        pass

    # 2. Fallback delimiter parsing if JSON was not applicable
    if not parsed_candidates:
        tokens = re.split(r'[;,\r\n"\'\[\]\{\}\s]+', raw_content)
        parsed_candidates = tokens

    parsed = []
    seen = set()

    for token in parsed_candidates:
        cleaned = str(token).strip()
        # Ensure token looks like a phone number (at least 7 digits)
        digits_only = re.sub(r'\D', '', cleaned)
        if len(digits_only) >= 7 and cleaned not in seen:
            seen.add(cleaned)
            parsed.append(cleaned)

    return parsed

def parse_excel_bytes(file_bytes: bytes) -> List[str]:
    """
    Parses cell values from Excel (.xlsx / .xls) files and extracts phone numbers.
    Also applies Moldovan phone number normalization rules.
    """
    raw_cells = []
    try:
        import openpyxl
        from io import BytesIO
        wb = openpyxl.load_workbook(BytesIO(file_bytes), data_only=True)
        for sheet in wb.worksheets:
            for row in sheet.iter_rows(values_only=True):
                for val in row:
                    if val is not None:
                        raw_cells.append(str(val))
    except Exception as e:
        logger.warning(f"openpyxl Excel parsing failed, using fallback string parsing: {e}")
        raw_text = file_bytes.decode("utf-8", errors="ignore")
        return parse_phone_numbers(raw_text)

    full_text = "\n".join(raw_cells)
    from backend.ai_extractor import extract_phones_with_regex
    return extract_phones_with_regex(full_text)

class TelegramContactChecker:
    def __init__(self):
        self.client: Optional[TelegramClient] = None
        self.api_id: Optional[int] = None
        self.api_hash: Optional[str] = None
        self._phone_code_hash: Optional[str] = None

    def initialize_config(self):
        """Loads and verifies credentials from .env."""
        self.api_id, self.api_hash = load_credentials()

    def get_client(self) -> TelegramClient:
        """Returns or instantiates Telethon client with persistent session file."""
        if not self.api_id or not self.api_hash:
            self.initialize_config()
            
        if self.client is None:
            self.client = TelegramClient(str(SESSION_FILE), self.api_id, self.api_hash)
        return self.client

    async def check_authorization(self) -> bool:
        """Checks if client session is valid and authorized."""
        client = self.get_client()
        try:
            if not client.is_connected():
                await client.connect()
            is_auth = await client.is_user_authorized()
            logger.info(f"Telegram Authorization Status: {'AUTHORIZED' if is_auth else 'NOT AUTHORIZED'}")
            return is_auth
        except ApiIdInvalidError:
            raise ConfigError("Invalid API_ID or API_HASH. Please check your credentials in .env.")
        except Exception as e:
            logger.error(f"Error checking authorization: {e}")
            raise CriticalTelegramError(f"Telegram connection error: {str(e)}")

    async def send_auth_code(self, phone_number: str) -> str:
        """Sends verification code to user phone."""
        client = self.get_client()
        if not client.is_connected():
            await client.connect()
        res = await client.send_code_request(phone_number)
        self._phone_code_hash = res.phone_code_hash
        logger.info(f"Verification code requested for phone: {phone_number[:4]}***")
        return res.phone_code_hash

    async def sign_in_with_code(self, phone_number: str, code: str, password: Optional[str] = None) -> bool:
        """Completes Telegram authorization using code and optional 2FA password."""
        client = self.get_client()
        if not client.is_connected():
            await client.connect()

        try:
            await client.sign_in(phone=phone_number, code=code, phone_code_hash=self._phone_code_hash)
            logger.info("Telegram sign in successful!")
            return True
        except SessionPasswordNeededError:
            if not password:
                raise SessionPasswordNeededError("2FA Password is required for this account.")
            await client.sign_in(password=password)
            logger.info("Telegram sign in with 2FA password successful!")
            return True
        except Exception as e:
            logger.error(f"Sign in failed: {e}")
            raise

    async def disconnect(self):
        """Cleanly disconnects the Telegram client."""
        if self.client and self.client.is_connected():
            await self.client.disconnect()
            logger.info("Telegram client disconnected.")

    async def check_batch(self, batch_phones: List[str], batch_start_idx: int) -> List[Dict[str, Any]]:
        """
        Executes contacts.ImportContacts for a batch of numbers.
        Returns list of result objects matching format:
        {
          "phone": str,
          "status": "FOUND" | "NOT_FOUND" | "ERROR",
          "user_id": int | None,
          "username": str | None,
          "first_name": str | None,
          "last_name": str | None,
          "error": str | None
        }
        """
        client = self.get_client()
        if not client.is_connected():
            await client.connect()

        if not await client.is_user_authorized():
            raise CriticalTelegramError("Telegram client is not authorized. Please complete authorization.")

        input_contacts = []
        id_to_phone: Dict[int, str] = {}

        for idx, phone in enumerate(batch_phones):
            client_id = batch_start_idx + idx
            id_to_phone[client_id] = phone
            input_contacts.append(
                InputPhoneContact(
                    client_id=client_id,
                    phone=phone,
                    first_name="",
                    last_name=""
                )
            )

        logger.info(f"Executing ImportContacts for batch of {len(batch_phones)} contacts...")
        
        try:
            response = await client(ImportContactsRequest(contacts=input_contacts))
        except FloodWaitError as e:
            logger.warning(f"Telegram FloodWaitError encountered: Must wait {e.seconds} seconds.")
            raise e
        except Exception as e:
            logger.error(f"Batch ImportContacts failed with exception: {e}")
            # Return error for all items in batch
            results = []
            for phone in batch_phones:
                results.append({
                    "phone": phone,
                    "status": "ERROR",
                    "user_id": None,
                    "username": None,
                    "first_name": None,
                    "last_name": None,
                    "error": f"Telegram API error: {str(e)}"
                })
            return results

        # Process returned users
        users_by_id: Dict[int, User] = {u.id: u for u in response.users if isinstance(u, User)}
        
        # Map imported contacts: imported contact holds (client_id, user_id)
        found_by_client_id: Dict[int, User] = {}
        imported_contacts_to_delete = []

        for imp in response.imported:
            user_obj = users_by_id.get(imp.user_id)
            if user_obj:
                found_by_client_id[imp.client_id] = user_obj
                imported_contacts_to_delete.append(user_obj)

        results = []
        for client_id, phone in id_to_phone.items():
            if client_id in found_by_client_id:
                user = found_by_client_id[client_id]
                results.append({
                    "phone": phone,
                    "status": "FOUND",
                    "user_id": user.id,
                    "username": user.username if user.username else "—",
                    "first_name": user.first_name if user.first_name else "—",
                    "last_name": user.last_name if user.last_name else "—",
                    "error": None
                })
            else:
                # Successfully checked contact, but user was not found
                results.append({
                    "phone": phone,
                    "status": "NOT_FOUND",
                    "user_id": None,
                    "username": None,
                    "first_name": None,
                    "last_name": None,
                    "error": "Telegram user was not found for this phone number"
                })

        # Cleanup imported contacts from address book to avoid polluting contact list
        if imported_contacts_to_delete:
            try:
                await client(DeleteContactsRequest(id=imported_contacts_to_delete))
            except Exception as e:
                logger.warning(f"Failed to cleanup imported contacts from address book: {e}")

        return results
