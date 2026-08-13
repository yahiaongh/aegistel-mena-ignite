# backend/app/text_normalization.py
import datetime as dt
import re
from typing import Any

from app.core.constants import ISO_COUNTRY_NAMES

try:
    import num2words  # type: ignore
except Exception:  # pragma: no cover - optional dependency fallback
    num2words = None

try:
    import phonenumbers  # type: ignore
except Exception:  # pragma: no cover - optional dependency fallback
    phonenumbers = None


class TTSTextNormalizer:
    """Normalize spoken text for TTS, with a focus on phone numbers and dates."""

    _PHONE_PATTERN = re.compile(r"\+?\d[\d\s().-]{6,}\d")
    ISO_COUNTRY_NAMES = ISO_COUNTRY_NAMES

    def normalize(self, text: str) -> str:
        if not text:
            return ""
        normalized = text.strip()
        normalized = self._normalize_urls_and_emails(normalized)
        normalized = self._normalize_iso_datetimes(normalized)
        normalized = self._normalize_country_codes(normalized)
        normalized = self._normalize_uuids(normalized)
        normalized = self._normalize_phone_numbers(normalized)
        normalized = self._normalize_currency(normalized)
        normalized = self._normalize_dates(normalized)
        normalized = self._normalize_numbers(normalized)
        return normalized

    def _normalize_urls_and_emails(self, text: str) -> str:
        if not text:
            return text
        text = re.sub(r"https?://\S+", "[url]", text)
        return re.sub(r"\b[\w.%+-]+@[\w.-]+\.[A-Za-z]{2,}\b", "[email]", text)

    def _normalize_phone_numbers(self, text: str) -> str:
        if not text:
            return text

        def repl(match: Any) -> str:
            raw = match.group(0)
            if phonenumbers is not None:
                try:
                    parsed = phonenumbers.parse(raw, "ZZ")
                    if phonenumbers.is_valid_number(parsed):
                        digits = list(re.sub(r"\D", "", raw))
                        return f"plus {', '.join(digits)}"
                except Exception:
                    pass
            digits = re.sub(r"\D", "", raw)
            if not digits:
                return raw
            if len(digits) >= 10:
                lettered = ", ".join(list(digits))
                return f"plus {lettered}"
            return raw

        return self._PHONE_PATTERN.sub(repl, text)

    def _normalize_currency(self, text: str) -> str:
        if not text:
            return text
        return re.sub(r"\$([0-9,\.]+)", self._replace_currency, text)

    def _normalize_iso_datetimes(self, text: str) -> str:
        if not text:
            return text
        iso_pattern = r"\b(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d+)?(?:[+-]\d{2}:\d{2}|Z)?\b"
        months = [
            "January",
            "February",
            "March",
            "April",
            "May",
            "June",
            "July",
            "August",
            "September",
            "October",
            "November",
            "December",
        ]

        def replace_iso(match: Any) -> str:
            year, month, day, hour, minute, _second = match.groups()
            try:
                month_name = months[int(month) - 1]
            except (IndexError, ValueError):
                return match.group(0)
            day_int = int(day)
            year_int = int(year)
            hour_int = int(hour)
            period = "AM" if hour_int < 12 else "PM"
            hour_12 = hour_int % 12 or 12
            minute_word = "" if minute == "00" else f" {self._number_to_words(minute)}"
            if num2words is not None:
                try:
                    day_word = num2words.num2words(day_int, to="ordinal")
                    year_word = self._year_to_words(year_int)
                    hour_word = num2words.num2words(hour_12)
                except Exception:
                    day_word = str(day_int)
                    year_word = str(year_int)
                    hour_word = str(hour_12)
            else:
                day_word = str(day_int)
                year_word = str(year_int)
                hour_word = str(hour_12)
            return f"{month_name} {day_word}, {year_word} at {hour_word}{minute_word} {period}"

        return re.sub(iso_pattern, replace_iso, text)

    def _normalize_country_codes(self, text: str) -> str:
        if not text:
            return text

        def replace_codes(match: Any) -> str:
            codes = re.findall(r"[A-Z]{2}", match.group(1))
            if not codes:
                return "no country specified"
            names = [self.ISO_COUNTRY_NAMES.get(code, f"country code {' '.join(list(code))}") for code in codes]
            return " and ".join(names)

        return re.sub(r"countryIsoCodes:\s*\[([^\]]*)\]", lambda m: f"country: {replace_codes(m)}", text)

    def _normalize_uuids(self, text: str) -> str:
        if not text:
            return text
        uuid_pattern = r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"

        def replace_uuid(match: Any) -> str:
            full = match.group(0).replace("-", "").upper()
            start = ", ".join([char.lower() for char in full[:3]])
            end = ", ".join([char if char.isdigit() else char.upper() for char in full[-3:]])
            return f"an identifier starting with {start}, and ending with {end}"

        return re.sub(uuid_pattern, replace_uuid, text)

    def _normalize_numbers(self, text: str) -> str:
        if not text:
            return text

        def repl(match: Any) -> str:
            raw = match.group(0)
            if "." in raw:
                whole, frac = raw.split(".", 1)
                whole_words = self._number_to_words(whole)
                frac_words = ", ".join(self._digit_words(frac))
                return f"{whole_words} point {frac_words}"
            return raw

        return re.sub(r"\b\d+\.\d+\b", repl, text)

    def _digit_words(self, digits: str) -> list[str]:
        return [self._number_to_words(d) for d in digits]

    def _number_to_words(self, value: str) -> str:
        if num2words is not None:
            try:
                if len(value) > 1 and value.startswith("0"):
                    return value
                return num2words.num2words(int(value), lang="en")
            except Exception:
                pass
        return value

    def _year_to_words(self, year: int) -> str:
        if num2words is not None:
            try:
                if 2000 <= year < 2100:
                    return f"two thousand {num2words.num2words(year % 100, lang='en')}"
                return num2words.num2words(year, to="year")
            except Exception:
                pass
        return str(year)

    def _replace_currency(self, match: Any) -> str:
        value = match.group(1)
        if num2words is not None:
            try:
                return f"{num2words.num2words(float(value.replace(',', '')), lang='en')} dollars"
            except Exception:
                pass
        return f"{value} dollars"

    def _normalize_dates(self, text: str) -> str:
        if not text:
            return text
        return re.sub(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b", r"\1 slash \2 slash \3", text)
