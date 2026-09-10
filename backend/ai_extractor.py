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

def extract_phones_with_regex(raw_text: str) -> List[str]:
    """
    Fallback deterministic phone extractor from raw text.
    Extracts patterns matching phone numbers and applies Moldova normalization.
    """
    # Regex matching phone candidates: optional +, digits, spaces, dashes
    candidates = re.findall(r'\+?\d[\d\s\-\(\)]{6,14}\d', raw_text)
    extracted = []
    seen = set()

    for cand in candidates:
        # Strip internal spaces, parens, dashes
        clean = re.sub(r'[\s\-\(\)]', '', cand)
        normalized = normalize_moldova_phone(clean)
        digits_only = re.sub(r'\D', '', normalized)
        
        if len(digits_only) >= 7 and normalized not in seen:
            seen.add(normalized)
            extracted.append(normalized)

    return extracted

def extract_and_normalize_phones(raw_text: str, api_key: Optional[str] = None) -> List[str]:
    """
    Extracts phone numbers using OpenAI API if key is provided/configured,
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
                "Extract all phone numbers from the following raw text. "
                "Return ONLY a JSON array of strings containing the extracted phone numbers. "
                "Do not include any explanation or markdown wrapping outside the JSON array.\n\n"
                f"Raw Text:\n\"\"\"\n{raw_text}\n\"\"\""
            )

            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": "You extract phone numbers from text and output JSON arrays."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.0,
            )

            content = response.choices[0].message.content.strip()
            # Remove ```json ... ``` code fence if present
            content = re.sub(r'^```(?:json)?\s*', '', content)
            content = re.sub(r'\s*```$', '', content)

            data = json.loads(content)
            if isinstance(data, list):
                ai_extracted = []
                seen = set()
                for item in data:
                    item_str = str(item)
                    normalized = normalize_moldova_phone(item_str)
                    if normalized not in seen:
                        seen.add(normalized)
                        ai_extracted.append(normalized)
                if ai_extracted:
                    logger.info(f"OpenAI successfully extracted {len(ai_extracted)} phone numbers.")
                    return ai_extracted

        except Exception as e:
            logger.warning(f"OpenAI extraction failed, falling back to regex: {e}")

    # Fallback to regex extraction
    logger.info("Using regex phone extraction fallback.")
    return extract_phones_with_regex(raw_text)
