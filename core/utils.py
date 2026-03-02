import hashlib
import re
import uuid
from datetime import date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from django.utils.text import slugify
from phonenumbers import NumberParseException, PhoneNumberFormat, format_number, parse


def to_decimal(value, precision: str = "0.01") -> Decimal:
    """Convert any numeric value into a quantized Decimal."""
    try:
        decimal_value = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("Invalid decimal value") from exc

    return decimal_value.quantize(Decimal(precision), rounding=ROUND_HALF_UP)


def normalize_kenyan_phone(phone_number: str) -> str:
    """
    Normalize Kenyan phone numbers to E.164.

    Accepted examples:
    - 0712345678
    - 712345678
    - 254712345678
    - +254712345678
    - 0112345678
    - +254112345678
    """
    if not phone_number:
        raise ValueError("Phone number is required.")

    value = re.sub(r"[^\d+]", "", str(phone_number).strip())

    if value.startswith("+"):
        digits = value[1:]
    else:
        digits = value

    if digits.startswith("0") and len(digits) == 10 and digits[1] in {"7", "1"}:
        digits = f"254{digits[1:]}"
    elif len(digits) == 9 and digits[0] in {"7", "1"}:
        digits = f"254{digits}"
    elif len(digits) == 12 and digits.startswith("254") and digits[3] in {"7", "1"}:
        pass
    else:
        raise ValueError(
            "Enter a valid Kenyan phone number in +2547XXXXXXXX or +2541XXXXXXXX format."
        )

    normalized = f"+{digits}"
    if len(normalized) != 13:
        raise ValueError(
            "Enter a valid Kenyan phone number in +2547XXXXXXXX or +2541XXXXXXXX format."
        )
    return normalized


def generate_unique_code(prefix: str = "", length: int = 8) -> str:
    unique_id = str(uuid.uuid4()).replace("-", "")[:length].upper()
    return f"{prefix}-{unique_id}" if prefix else unique_id


def generate_idempotency_key(chama_id, data) -> str:
    data_str = f"{chama_id}:{data}"
    return hashlib.sha256(data_str.encode()).hexdigest()[:32]


def normalize_phone_number(phone_number: str, country: str = "KE") -> str:
    """Normalize phone numbers to E.164 format."""
    if country == "KE":
        return normalize_kenyan_phone(phone_number)

    try:
        parsed = parse(phone_number, country)
        return format_number(parsed, PhoneNumberFormat.E164)
    except NumberParseException:
        return phone_number.strip() if phone_number else ""


def generate_member_number(chama_name: str, member_count: int) -> str:
    chama_slug = slugify(chama_name)[:3].upper()
    member_num = str(member_count + 1).zfill(4)
    return f"{chama_slug}{member_num}"


def calculate_age(birth_date: date) -> int:
    today = timezone.localdate()
    return today.year - birth_date.year - (
        (today.month, today.day) < (birth_date.month, birth_date.day)
    )


def format_currency(amount, currency: str = "KES") -> str:
    return f"{currency} {to_decimal(amount):,.2f}"


def get_file_size_mb(file) -> Decimal:
    return to_decimal(file.size / (1024 * 1024), precision="0.0001")


def get_current_timezone() -> datetime:
    return timezone.now()


def calculate_loan_installment(
    principal,
    interest_rate,
    duration_months: int,
    interest_type: str = "flat",
) -> Decimal:
    principal_amount = to_decimal(principal)
    rate = to_decimal(interest_rate, precision="0.0001")
    months = Decimal(duration_months)

    if duration_months <= 0:
        raise ValueError("duration_months must be positive")

    if interest_type == "flat":
        total_interest = principal_amount * (rate / Decimal("100")) * (months / Decimal("12"))
        total_amount = principal_amount + total_interest
        return to_decimal(total_amount / months)

    if interest_type == "reducing":
        monthly_rate = rate / Decimal("100") / Decimal("12")
        factor = (Decimal("1") + monthly_rate) ** duration_months
        installment = principal_amount * (monthly_rate * factor) / (factor - Decimal("1"))
        return to_decimal(installment)

    return Decimal("0.00")


def generate_receipt_code(chama_id, transaction_type: str) -> str:
    timestamp = timezone.now().strftime("%Y%m%d%H%M%S")
    unique_part = str(uuid.uuid4())[:6].upper()
    return f"{chama_id}-{transaction_type}-{timestamp}-{unique_part}"


def validate_decimal_amount(amount) -> Decimal:
    decimal_amount = to_decimal(amount)
    if decimal_amount <= Decimal("0"):
        raise ValueError("Amount must be positive")
    return decimal_amount


def parse_iso_date(value: str) -> date | None:
    if not value:
        return None
    parsed = parse_date(value)
    if parsed:
        return parsed
    parsed_dt = parse_datetime(value)
    return parsed_dt.date() if parsed_dt else None


def start_of_day(value: date | datetime | None = None) -> datetime:
    base_date = value.date() if isinstance(value, datetime) else value
    base_date = base_date or timezone.localdate()
    start = datetime.combine(base_date, time.min)
    return timezone.make_aware(start, timezone.get_current_timezone())


def end_of_day(value: date | datetime | None = None) -> datetime:
    base_date = value.date() if isinstance(value, datetime) else value
    base_date = base_date or timezone.localdate()
    end = datetime.combine(base_date, time.max)
    return timezone.make_aware(end, timezone.get_current_timezone())


def month_bounds(value: date | datetime | None = None) -> tuple[date, date]:
    if isinstance(value, datetime):
        value = value.date()

    reference = value or timezone.localdate()
    first_day = reference.replace(day=1)

    if first_day.month == 12:
        next_month = first_day.replace(year=first_day.year + 1, month=1)
    else:
        next_month = first_day.replace(month=first_day.month + 1)

    last_day = next_month - timedelta(days=1)
    return first_day, last_day
