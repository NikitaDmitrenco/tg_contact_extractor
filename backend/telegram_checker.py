import os
import re
import asyncio
import logging
from typing import List, Dict, Any, Optional, Tuple
from pathlib import Path
from dotenv import load_dotenv

from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.functions.contacts import ImportContactsRequest, DeleteContactsRequest
from telethon.tl.functions.users import GetFullUserRequest
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

def clean_session_string(raw: str) -> str:
    """Strips quotes, spaces and line breaks introduced by copy-pasting a StringSession key."""
    return re.sub(r'''[\s"']+''', '', raw or "")

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
    """
    parsed_candidates = []
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

    if not parsed_candidates:
        tokens = re.split(r'[;,\r\n"\'\[\]\{\}\s]+', raw_content)
        parsed_candidates = tokens

    parsed = []
    seen = set()

    for token in parsed_candidates:
        cleaned = str(token).strip()
        digits_only = re.sub(r'\D', '', cleaned)
        if len(digits_only) >= 7 and cleaned not in seen:
            seen.add(cleaned)
            parsed.append(cleaned)

    return parsed

def parse_excel_bytes(file_bytes: bytes) -> List[str]:
    """
    Parses cell values from Excel (.xlsx / .xls) files and extracts phone numbers.
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
    extracted_dicts = extract_phones_with_regex(full_text)
    return [d["phone"] for d in extracted_dicts if "phone" in d]

async def extract_user_birthday(client: TelegramClient, user_obj: User) -> Optional[str]:
    """
    Fetches full user profile to extract Birthday if visible.
    """
    try:
        full_res = await client(GetFullUserRequest(user_obj))
        full_user = getattr(full_res, 'full_user', None)
        birthday = getattr(full_user, 'birthday', None) if full_user else None
        if birthday:
            day = getattr(birthday, 'day', None)
            month = getattr(birthday, 'month', None)
            year = getattr(birthday, 'year', None)
            if day and month:
                if year:
                    return f"{day:02d}.{month:02d}.{year}"
                return f"{day:02d}.{month:02d}"
    except Exception as e:
        logger.debug(f"Could not fetch birthday for user {user_obj.id}: {e}")
    return None

class TelegramContactChecker:
    def __init__(self):
        self.client: Optional[TelegramClient] = None
        self.api_id: Optional[int] = None
        self.api_hash: Optional[str] = None
        self._phone_code_hash: Optional[str] = None
        self.public_base_url: str = ""

    def initialize_config(self):
        """Loads and verifies credentials from .env."""
        self.api_id, self.api_hash = load_credentials()

    def get_client(self) -> TelegramClient:
        """Returns or instantiates Telethon client with persistent session file or StringSession."""
        if not self.api_id or not self.api_hash:
            self.initialize_config()
            
        if self.client is None:
            # Tolerate quotes, spaces and line breaks introduced by copy-pasting the key.
            session_str = clean_session_string(os.getenv("TG_SESSION_STRING", ""))
            if session_str:
                # A real StringSession is a long base64 blob (~350 chars); anything else is a misconfiguration.
                try:
                    if len(session_str) < 100:
                        raise ValueError("value is too short to be a Telethon StringSession")
                    session = StringSession(session_str)
                    logger.info("Initializing TelegramClient using TG_SESSION_STRING environment variable.")
                    self.client = TelegramClient(session, self.api_id, self.api_hash)
                except Exception as e:
                    logger.warning(f"TG_SESSION_STRING is invalid ({e}); falling back to session file {SESSION_FILE}")
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
        try:
            hash_file = SESSIONS_DIR / "tg_code_hash.txt"
            hash_file.write_text(res.phone_code_hash, encoding="utf-8")
        except Exception as e:
            logger.warning(f"Could not persist phone_code_hash: {e}")
        logger.info(f"Verification code requested for phone: {phone_number[:4]}***")
        return res.phone_code_hash

    async def sign_in_with_code(self, phone_number: str, code: str, password: Optional[str] = None, phone_code_hash: Optional[str] = None) -> bool:
        """Completes Telegram authorization using code and optional 2FA password."""
        client = self.get_client()
        if not client.is_connected():
            await client.connect()

        code_hash = phone_code_hash or self._phone_code_hash
        if not code_hash:
            try:
                hash_file = SESSIONS_DIR / "tg_code_hash.txt"
                if hash_file.exists():
                    code_hash = hash_file.read_text(encoding="utf-8").strip()
            except Exception as e:
                logger.warning(f"Could not read phone_code_hash from file: {e}")

        try:
            await client.sign_in(phone=phone_number, code=code, phone_code_hash=code_hash)
            logger.info("Telegram sign in successful!")
            session_str = ""
            try:
                session_str = StringSession.save(client.session)
                if session_str:
                    logger.info("Generated TG_SESSION_STRING (shown in UI, not logged).")
            except Exception as e:
                logger.debug(f"Could not export StringSession: {e}")
            return session_str or "AUTHORIZED"
        except SessionPasswordNeededError:
            if not password:
                raise SessionPasswordNeededError("2FA Password is required for this account.")
            await client.sign_in(password=password)
            logger.info("Telegram sign in with 2FA password successful!")
            session_str = ""
            try:
                session_str = StringSession.save(client.session)
            except Exception as e:
                logger.debug(f"Could not export StringSession: {e}")
            return session_str or "AUTHORIZED"
        except Exception as e:
            logger.error(f"Sign in failed: {e}")
            raise

    async def disconnect(self):
        """Cleanly disconnects the Telegram client."""
        if self.client and self.client.is_connected():
            await self.client.disconnect()
            logger.info("Telegram client disconnected.")

    def set_public_base_url(self, url: str):
        """Base URL used to build absolute /api/photo links (set from the incoming request)."""
        self.public_base_url = (url or "").rstrip("/")

    def _photo_url(self, user: User, photo_id: int) -> str:
        return f"{self.public_base_url}/api/photo/{user.id}/{user.access_hash}/{photo_id}"

    async def _resolve_by_username(self, client: TelegramClient, username: str) -> Tuple[Optional[User], str]:
        """Resolves a public username. Returns (user, "") or (None, reason) when it is missing or not a user."""
        try:
            entity = await client.get_entity(username)
            if isinstance(entity, User):
                return entity, ""
            logger.info(f"@{username} resolved to a non-user entity ({type(entity).__name__}), skipping.")
            return None, f"@{username} is a channel/group, not a user"
        except FloodWaitError:
            raise
        except Exception as e:
            logger.info(f"Could not resolve @{username}: {e}")
            return None, f"Username @{username} does not exist"

    async def _enrich_found_user(self, client: TelegramClient, user: User, item_dict: Dict[str, Any], found_via: str) -> Dict[str, Any]:
        """Builds a FOUND result: input fields win, missing ones are filled from the Telegram profile."""
        input_fn = (item_dict.get("first_name") or "").strip()
        input_ln = (item_dict.get("last_name") or "").strip()
        input_un = (item_dict.get("username") or "").strip().lstrip("@")
        input_bd = (item_dict.get("birthday") or "").strip()

        profile_fn = getattr(user, 'first_name', None) or ""
        profile_ln = getattr(user, 'last_name', None) or ""
        profile_un = getattr(user, 'username', None) or ""

        # Refresh from the entity cache: ImportContacts may return the contact-book name instead of the profile name
        try:
            entity = await client.get_entity(user.id)
            if isinstance(entity, User):
                profile_fn = entity.first_name or profile_fn
                profile_ln = entity.last_name or profile_ln
                profile_un = entity.username or profile_un
                user = entity
        except Exception as e:
            logger.debug(f"get_entity refresh failed for user {user.id}: {e}")

        birthday_str = await extract_user_birthday(client, user)

        photos: List[str] = []
        try:
            for photo in await client.get_profile_photos(user):
                photos.append(self._photo_url(user, photo.id))
        except FloodWaitError:
            raise
        except Exception as e:
            logger.warning(f"Could not list profile photos for user {user.id}: {e}")

        return {
            "phone": item_dict.get("phone", ""),
            "status": "FOUND",
            "found_via": found_via,
            "user_id": user.id,
            "username": input_un or profile_un,
            "first_name": input_fn or profile_fn,
            "last_name": input_ln or profile_ln,
            "birthday": input_bd or birthday_str or "",
            "photos": photos,
            "error": None
        }

    @staticmethod
    def _not_found_result(item_dict: Dict[str, Any], status: str, error: Optional[str]) -> Dict[str, Any]:
        return {
            "phone": item_dict.get("phone", ""),
            "status": status,
            "found_via": "",
            "user_id": None,
            "username": (item_dict.get("username") or "").strip().lstrip("@"),
            "first_name": (item_dict.get("first_name") or "").strip(),
            "last_name": (item_dict.get("last_name") or "").strip(),
            "birthday": (item_dict.get("birthday") or "").strip(),
            "photos": [],
            "error": error
        }

    async def check_batch(self, batch_items: List[Any], batch_start_idx: int) -> List[Dict[str, Any]]:
        """
        Looks up a batch of contact items (dicts or phone strings) in Telegram.
        Items with a phone go through contacts.ImportContacts; items without a phone (or not found by
        phone) that carry a username are resolved via the public username. Found users are enriched
        with profile name, username, birthday and profile photo links.
        """
        client = self.get_client()
        if not client.is_connected():
            await client.connect()

        if not await client.is_user_authorized():
            raise CriticalTelegramError("Telegram client is not authorized. Please complete authorization.")

        id_to_item: Dict[int, Dict[str, Any]] = {}
        input_contacts = []
        for idx, item in enumerate(batch_items):
            client_id = batch_start_idx + idx
            if isinstance(item, str):
                item_dict = {"phone": item, "first_name": "", "last_name": "", "username": "", "birthday": ""}
            else:
                item_dict = dict(item)
            id_to_item[client_id] = item_dict
            phone = (item_dict.get("phone") or "").strip()
            if phone:
                input_contacts.append(InputPhoneContact(client_id=client_id, phone=phone, first_name="", last_name=""))

        found_by_client_id: Dict[int, User] = {}
        imported_contacts_to_delete: List[User] = []
        retry_ids = set()

        if input_contacts:
            logger.info(f"Executing ImportContacts for {len(input_contacts)} phone(s) in batch of {len(batch_items)}...")
            try:
                response = await client(ImportContactsRequest(contacts=input_contacts))
            except FloodWaitError as e:
                logger.warning(f"Telegram FloodWaitError encountered: Must wait {e.seconds} seconds.")
                raise
            except Exception as e:
                logger.error(f"Batch ImportContacts failed with exception: {e}")
                return [self._not_found_result(item, "ERROR", f"Telegram API error: {str(e)}") for item in id_to_item.values()]

            users_by_id: Dict[int, User] = {u.id: u for u in response.users if isinstance(u, User)}
            for imp in response.imported:
                user_obj = users_by_id.get(imp.user_id)
                if user_obj:
                    found_by_client_id[imp.client_id] = user_obj
                    imported_contacts_to_delete.append(user_obj)
            retry_ids = set(getattr(response, "retry_contacts", []) or [])

        results = []
        for client_id, item_dict in id_to_item.items():
            username = (item_dict.get("username") or "").strip().lstrip("@")
            phone = (item_dict.get("phone") or "").strip()
            user = found_by_client_id.get(client_id)
            found_via = "phone" if user else ""

            username_error = ""
            if user is None and username:
                user, username_error = await self._resolve_by_username(client, username)
                found_via = "username" if user else ""

            if user is not None:
                results.append(await self._enrich_found_user(client, user, item_dict, found_via))
            elif client_id in retry_ids:
                results.append(self._not_found_result(item_dict, "ERROR", "Telegram asked to retry this number later (retry_contacts)"))
            elif phone and username:
                results.append(self._not_found_result(item_dict, "NOT_FOUND", f"Not found by phone (hidden by privacy or not registered); {username_error}"))
            elif phone:
                results.append(self._not_found_result(item_dict, "NOT_FOUND", "Telegram user was not found for this phone number (hidden by privacy or not registered)"))
            elif username:
                results.append(self._not_found_result(item_dict, "NOT_FOUND", username_error))
            else:
                results.append(self._not_found_result(item_dict, "ERROR", "No phone or username to search by"))

        # Cleanup imported contacts from address book to avoid polluting contact list
        if imported_contacts_to_delete:
            try:
                await client(DeleteContactsRequest(id=imported_contacts_to_delete))
            except Exception as e:
                logger.warning(f"Failed to cleanup imported contacts from address book: {e}")

        return results
