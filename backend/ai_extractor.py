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

def extract_phones_with_regex(raw_text: str) -> List[Dict[str, str]]:
    """
    Fallback deterministic contact extractor from raw text line-by-line.
    Extracts phone numbers, normalized according to Moldova rules,
    and captures surrounding text on the same line as first_name and last_name.
    """
    extracted = []
    seen_phones = set()

    lines = raw_text.splitlines()
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue

        # Find phone candidates in this line
        phone_matches = re.finditer(r'\+?\d[\d\s\-\(\)]{6,14}\d', stripped)
        phone_matches_list = list(phone_matches)
        if not phone_matches_list:
            continue

        for match in phone_matches_list:
            raw_phone = match.group(0)
            clean_phone = re.sub(r'[\s\-\(\)]', '', raw_phone)
            normalized = normalize_moldova_phone(clean_phone)
            digits_only = re.sub(r'\D', '', normalized)

            if len(digits_only) < 7 or normalized in seen_phones:
                continue

            seen_phones.add(normalized)

            # Get remaining text on line without phone candidate
            line_without_phone = stripped.replace(raw_phone, " ")
            # Tokenize words
            tokens = [t.strip(",;:") for t in line_without_phone.split() if t.strip(",;:")]

            first_name = ""
            last_name = ""
            username = ""
            birthday = ""

            words = []
            for token in tokens:
                if token.startswith("@"):
                    username = token.lstrip("@")
                elif re.match(r'^\d{2}\.\d{2}(\.\d{4})?$', token):
                    birthday = token
                elif re.search(r'[a-zA-Zа-яА-ЯёЁа-яa-z]', token):
                    words.append(token)

            if len(words) >= 1:
                first_name = words[0]
            if len(words) >= 2:
                last_name = words[1]

            extracted.append({
                "phone": normalized,
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
                "You are an expert data extraction assistant. "
                "Extract all contact records from the following text line by line. "
                "For each contact, extract: 'phone' (normalized), 'first_name', 'last_name', 'username', 'birthday'. "
                "If a field is not present in the input line, set its value to empty string ''. "
                "Return ONLY a JSON array of objects with keys: 'phone', 'first_name', 'last_name', 'username', 'birthday'. "
                "Do not include markdown outside the JSON.\n\n"
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
                    if not isinstance(item, dict) or "phone" not in item:
                        continue
                    normalized = normalize_moldova_phone(str(item["phone"]))
                    if normalized not in seen:
                        seen.add(normalized)
                        ai_extracted.append({
                            "phone": normalized,
                            "first_name": str(item.get("first_name", "") or "").strip(),
                            "last_name": str(item.get("last_name", "") or "").strip(),
                            "username": str(item.get("username", "") or "").strip().lstrip("@"),
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
