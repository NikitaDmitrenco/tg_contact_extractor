import os
import re
import json
import logging
from typing import List, Optional
from dotenv import load_dotenv

logger = logging.getLogger("ai_extractor")

def normalize_moldova_phone(phone_str: str) -> str:
    """
    Normalizes phone numbers according to user rules:
    1. If number starts with '0' (e.g., 069123456), replace '0' with '+373' -> +37369123456
    2. If number starts with '6' or '7' (e.g., 69123456 or 78123456), prepend '+373' -> +37369123456
    3. If number starts with '373' without '+', add '+' -> +373...
    4. International formatted numbers (+...) are preserved.
    """
    cleaned = phone_str.strip()
    if not cleaned:
        return cleaned

    # Already has leading +
    if cleaned.startswith("+"):
        return cleaned

    # Starts with 0 (e.g., 069123456 -> +37369123456)
    if cleaned.startswith("0") and len(cleaned) >= 8:
        return "+373" + cleaned[1:]

    # Starts with 6 or 7 and is Moldovan 8-digit mobile number (e.g. 69123456 -> +37369123456)
    if (cleaned.startswith("6") or cleaned.startswith("7")) and len(cleaned) == 8:
        return "+373" + cleaned

    # Starts with 373 (e.g. 37369123456 -> +37369123456)
    if cleaned.startswith("373"):
        return "+" + cleaned

    # Default fallback: add + if digits only
    if cleaned.isdigit():
        return "+" + cleaned

    return cleaned

from typing import List, Dict, Optional, Any

USERNAME_LINK_RE = re.compile(r'(?:https?://)?(?:t(?:elegram)?\.me|telegram\.dog)/(?:s/)?([A-Za-z0-9_]+)/?', re.I)
USERNAME_AT_RE = re.compile(r'(?<![\w.])@([A-Za-z0-9_]+)\b')
PHONE_RE = re.compile(r'\+?\d[\d\s\-\(\)]{6,14}\d')
BIRTHDAY_RE = re.compile(r'^\d{1,2}[.\-/]\d{1,2}(?:[.\-/]\d{2,4})?$')

def extract_username(text: str) -> str:
    """Returns the first Telegram username found as @name or t.me/name link, without '@'."""
    def valid(name: str) -> bool:
        # Telegram usernames are 5-32 chars (4 allowed for legacy names); longer strings are not usernames
        return 4 <= len(name) <= 32 and name.lower() not in ("joinchat", "addstickers", "share", "proxy")
    m = USERNAME_LINK_RE.search(text)
    if m and valid(m.group(1)):
        return m.group(1)
    m = USERNAME_AT_RE.search(text)
    return m.group(1) if m and valid(m.group(1)) else ""

def extract_phones_with_regex(raw_text: str) -> List[Dict[str, str]]:
    """
    Deterministic contact extractor, line by line.
    Each line may contain a phone number and/or a Telegram username (@name or t.me/name link);
    remaining words on the line are treated as first and last name, dd.mm(.yyyy) as birthday.
    A record needs at least a phone or a username.
    """
    extracted = []
    seen = set()

    for line in raw_text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue

        phone_matches = list(PHONE_RE.finditer(stripped))
        username = extract_username(stripped)
        if not phone_matches and not username:
            continue

        # Remove phones, links and @mentions, then treat the rest as name / birthday tokens
        rest = PHONE_RE.sub(" ", stripped)
        rest = USERNAME_LINK_RE.sub(" ", rest)
        rest = USERNAME_AT_RE.sub(" ", rest)
        tokens = [t.strip(",;:|") for t in rest.split() if t.strip(",;:|")]

        words = []
        birthday = ""
        for token in tokens:
            if BIRTHDAY_RE.match(token):
                birthday = token
            elif re.search(r'[A-Za-zА-Яа-яЁё]', token) and not re.match(r'https?:', token, re.I):
                words.append(token)
        first_name = words[0] if words else ""
        last_name = words[1] if len(words) > 1 else ""

        phones = []
        for m in phone_matches:
            normalized = normalize_moldova_phone(re.sub(r'[\s\-\(\)]', '', m.group(0)))
            if len(re.sub(r'\D', '', normalized)) >= 7:
                phones.append(normalized)
        if not phones:
            phones = [""]

        for phone in phones:
            key = phone or ("@" + username.lower())
            if key in seen:
                continue
            seen.add(key)
            extracted.append({
                "phone": phone,
                "first_name": first_name,
                "last_name": last_name,
                "username": username,
                "birthday": birthday
            })

    return extracted

def extract_and_normalize_phones(raw_text: str, api_key: Optional[str] = None) -> List[Dict[str, str]]:
    """
    Extracts structured contact records using OpenAI API if key is provided/configured,
    falling back to regex if key is missing or API fails.
    All extracted numbers are normalized using Moldovan rules.
    """
    key = api_key or os.getenv("OPENAI_API_KEY", "")
    key = key.strip()

    if key and key != "YOUR_OPENAI_API_KEY":
        try:
            import openai
            client = openai.OpenAI(api_key=key)

            prompt = (
                "You are an expert data extraction assistant for Telegram contact lookups. "
                "Extract every contact record from the text below. Records are usually one per line but may be free-form. "
                "For each contact return an object with keys: 'phone', 'first_name', 'last_name', 'username', 'birthday'.\n"
                "Rules:\n"
                "- 'phone': the phone number with all separators removed (keep a leading '+'). Empty string if absent.\n"
                "- 'username': Telegram username WITHOUT '@'. Take it from '@name' mentions or from profile links such as "
                "https://t.me/name, t.me/name, telegram.me/name. Empty string if absent.\n"
                "- 'first_name' / 'last_name': person's given name and surname as written. Empty string if absent.\n"
                "- 'birthday': date of birth in dd.mm.yyyy (or dd.mm if year unknown). Empty string if absent.\n"
                "- A record is valid if it has at least a phone OR a username; skip lines with neither.\n"
                "- Never invent data. Return ONLY a JSON array of objects, no markdown.\n\n"
                f"Raw Text:\n\"\"\"\n{raw_text}\n\"\"\""
            )

            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": "You extract structured contact records from text and output JSON arrays of objects."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.0,
            )

            content = response.choices[0].message.content.strip()
            content = re.sub(r'^```(?:json)?\s*', '', content)
            content = re.sub(r'\s*```$', '', content)

            data = json.loads(content)
            if isinstance(data, list):
                ai_extracted = []
                seen = set()
                for item in data:
                    if not isinstance(item, dict):
                        continue
                    raw_phone = re.sub(r'[\s\-\(\)]', '', str(item.get("phone", "") or ""))
                    normalized = normalize_moldova_phone(raw_phone) if raw_phone else ""
                    username = str(item.get("username", "") or "").strip()
                    username = extract_username(username) or username.lstrip("@")
                    if not normalized and not username:
                        continue
                    key = normalized or ("@" + username.lower())
                    if key not in seen:
                        seen.add(key)
                        ai_extracted.append({
                            "phone": normalized,
                            "first_name": str(item.get("first_name", "") or "").strip(),
                            "last_name": str(item.get("last_name", "") or "").strip(),
                            "username": username,
                            "birthday": str(item.get("birthday", "") or "").strip()
                        })
                if ai_extracted:
                    logger.info(f"OpenAI successfully extracted {len(ai_extracted)} structured contact records.")
                    return ai_extracted

        except Exception as e:
            logger.warning(f"OpenAI extraction failed, falling back to regex: {e}")

    # Fallback to regex extraction
    logger.info("Using regex structured contact extraction fallback.")
    return extract_phones_with_regex(raw_text)
