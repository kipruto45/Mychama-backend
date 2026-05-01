"""
Development-only email backend that captures emails for terminal inspection.

This backend:
1. Stores all sent emails in memory/file for later inspection
2. Prints formatted email content to terminal
3. Extracts and highlights OTPs, verification codes, and links
4. Categorizes emails by type

IMPORTANT: This should ONLY be used in development environments.
"""

import json
import os
import re
import threading
from datetime import datetime
from email.message import Message
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.mail.backends.base import BaseEmailBackend
from django.core.mail.message import EmailMessage, EmailMultiAlternatives

# Store emails in memory and optionally persist to file
_dev_emails: list[dict[str, Any]] = []
_dev_emails_lock = threading.Lock()

# File path for persistent storage
STORAGE_DIR = Path.home() / ".mychama-dev-emails"
STORAGE_FILE = STORAGE_DIR / "emails.json"
MAX_STORED_EMAILS = 100


def get_email_category(subject: str, body: str) -> str:
    """Detect email category from subject and body."""
    text = f"{subject} {body}".lower()
    
    if "verify" in text or "confirm" in text or "phone" in text:
        return "verification"
    if "otp" in text or "one time" in text or "verification code" in text:
        return "otp"
    if "password" in text or "reset" in text or "forgot" in text:
        return "password_reset"
    if "invite" in text or "join" in text or "chama" in text:
        return "invite"
    if "welcome" in text or "account" in text:
        return "welcome"
    if "announcement" in text or "notice" in text:
        return "announcement"
    if "loan" in text or "contribution" in text or "payment" in text:
        return "notification"
    
    return "unknown"


def extract_otp_codes(body: str) -> list[str]:
    """Extract OTP/verification codes from email body."""
    codes = []
    
    # Pattern 1: 4-8 digit numeric codes (common for OTPs)
    otp_patterns = [
        r'\b(\d{4})\b',  # 4 digits
        r'\b(\d{5})\b',  # 5 digits
        r'\b(\d{6})\b',  # 6 digits (most common)
        r'\b(\d{7})\b',  # 7 digits
        r'\b(\d{8})\b',  # 8 digits
    ]
    
    for pattern in otp_patterns:
        matches = re.findall(pattern, body)
        codes.extend(matches)
    
    # Pattern 2: Alphanumeric codes like ABC123XYZ
    alphanumeric = re.findall(r'\b([A-Z0-9]{4,12})\b', body.upper())
    codes.extend(alphanumeric)
    
    # Remove duplicates and common words that match
    codes = list(set(codes))
    exclude = {"THE", "AND", "FOR", "NOT", "WITH", "FROM", "YOUR", "CODE", "OTP"}
    codes = [c for c in codes if c not in exclude and len(c) >= 4]
    
    return codes[:5]  # Limit to 5 codes


def extract_links(body: str, html: str = "") -> list[dict[str, str]]:
    """Extract URLs from email body."""
    links = []
    
    # Combine body and html for link extraction
    text = f"{body} {html}"
    
    # Find URLs
    url_pattern = re.compile(
        r'https?://[^\s<>"\)]+|www\.[^\s<>"\)]+',
        re.IGNORECASE
    )
    
    found_urls = url_pattern.findall(text)
    
    for url in found_urls:
        link_type = "unknown"
        url_lower = url.lower()
        
        if "verify" in url_lower or "confirm" in url_lower:
            link_type = "verification"
        elif "reset" in url_lower or "password" in url_lower:
            link_type = "password_reset"
        elif "invite" in url_lower or "join" in url_lower:
            link_type = "invite"
        elif "login" in url_lower or "auth" in url_lower:
            link_type = "login"
        
        links.append({
            "url": url,
            "type": link_type
        })
    
    return links[:10]  # Limit to 10 links


def get_email_type_label(category: str) -> str:
    """Get human-readable label for email category."""
    labels = {
        "verification": "📧 Verification",
        "otp": "🔐 OTP Code",
        "password_reset": "🔑 Password Reset",
        "invite": "📨 Invite",
        "welcome": "👋 Welcome",
        "announcement": "📢 Announcement",
        "notification": "🔔 Notification",
        "unknown": "📬 Email",
    }
    return labels.get(category, "📬 Email")


def format_email_for_terminal(email_data: dict[str, Any]) -> str:
    """Format email for nice terminal output."""
    lines = []
    sep = "=" * 60
    
    category = email_data.get("category", "unknown")
    label = get_email_type_label(category)
    
    lines.append("")
    lines.append(sep)
    lines.append(f"  {label} #{email_data.get('index', '?')}")
    lines.append(sep)
    lines.append(f"  📅 {email_data.get('timestamp', 'Unknown time')}")
    lines.append(f"  👤 TO: {email_data.get('to', 'Unknown')}")
    lines.append(f"  📤 FROM: {email_data.get('from', 'Unknown')}")
    lines.append(f"  📝 SUBJECT: {email_data.get('subject', 'No subject')}")
    
    # Show extracted OTP codes
    otp_codes = email_data.get("otp_codes", [])
    if otp_codes:
        lines.append("")
        lines.append("  🔑 OTP/VERIFICATION CODES:")
        for code in otp_codes:
            lines.append(f"      {code}")
    
    # Show extracted links
    links = email_data.get("links", [])
    if links:
        lines.append("")
        lines.append("  🔗 LINKS:")
        for link in links:
            link_type_label = f" [{link['type']}]" if link['type'] != "unknown" else ""
            lines.append(f"      {link['url']}{link_type_label}")
    
    # Show body preview
    body = email_data.get("body", "")
    if body:
        lines.append("")
        lines.append("  📄 BODY PREVIEW:")
        # Show first 500 chars
        preview = body[:500] + "..." if len(body) > 500 else body
        for line in preview.split("\n")[:15]:  # Limit to 15 lines
            if line.strip():
                lines.append(f"      {line}")
    
    lines.append(sep)
    
    return "\n".join(lines)


def load_stored_emails() -> list[dict[str, Any]]:
    """Load emails from persistent storage."""
    global _dev_emails
    
    if STORAGE_FILE.exists():
        try:
            with open(STORAGE_FILE, "r") as f:
                stored = json.load(f)
                if isinstance(stored, list):
                    _dev_emails = stored
        except (json.JSONDecodeError, IOError):
            pass
    
    return _dev_emails


def save_emails_to_storage() -> None:
    """Persist emails to file."""
    STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    
    with _dev_emails_lock:
        # Keep only recent emails
        emails_to_save = _dev_emails[-MAX_STORED_EMAILS:]
    
    try:
        with open(STORAGE_FILE, "w") as f:
            json.dump(emails_to_save, f, indent=2, default=str)
    except IOError:
        pass  # Silently fail if we can't save


def store_email(email_data: dict[str, Any]) -> None:
    """Store email in memory and optionally persist."""
    global _dev_emails
    
    with _dev_emails_lock:
        # Add index to each email
        email_data["index"] = len(_dev_emails) + 1
        _dev_emails.append(email_data)
        
        # Trim old emails if we have too many
        if len(_dev_emails) > MAX_STORED_EMAILS:
            _dev_emails = _dev_emails[-MAX_STORED_EMAILS:]
    
    # Save to file asynchronously
    try:
        save_emails_to_storage()
    except Exception:
        pass


class DevEmailBackend(BaseEmailBackend):
    """
    Custom email backend for development that captures emails.
    
    Features:
    - Stores emails in memory and file
    - Formats output for terminal inspection
    - Extracts OTP codes and links
    - Categorizes emails by type
    """
    
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._fail_silently = kwargs.get("fail_silently", False)
        
        # Load existing emails on init
        load_stored_emails()
    
    def send_messages(self, messages: list[EmailMessage]) -> int:
        """Send messages - capture all to dev store."""
        count = 0
        
        for message in messages:
            if self._send(message):
                count += 1
        
        return count
    
    def _send(self, message: EmailMessage) -> bool:
        """Send a single message."""
        try:
            # Extract email data
            email_data = self._extract_email_data(message)
            
            # Store for later retrieval
            store_email(email_data)
            
            # Print to terminal
            print(format_email_for_terminal(email_data))
            
            return True
            
        except Exception as e:
            if self._fail_silently:
                return False
            raise
    
    def _extract_email_data(self, message: EmailMessage) -> dict[str, Any]:
        """Extract relevant data from email message."""
        # Get recipients
        to_list = message.to if hasattr(message, 'to') and message.to else []
        if isinstance(to_list, dict):
            to_list = list(to_list.values())
        
        # Get sender
        from_email = message.from_email if hasattr(message, 'from_email') else ""
        if hasattr(message, 'from'):
            from_email = getattr(message, 'from')
        
        # Get subject
        subject = message.subject if hasattr(message, 'subject') else "No subject"
        
        # Get body content
        body = ""
        html_body = ""
        
        if isinstance(message, EmailMultiAlternatives):
            # Plain text body
            if message.body:
                body = message.body
            
            # HTML body
            for alt in message.alternatives:
                if len(alt) >= 2 and alt[1] == "text/html":
                    html_body = alt[0]
                    break
        else:
            body = message.body or ""
        
        # Detect category
        category = get_email_category(subject, body)
        
        # Extract OTPs and links
        otp_codes = extract_otp_codes(body + html_body)
        links = extract_links(body, html_body)
        
        return {
            "timestamp": datetime.now().isoformat(),
            "to": ", ".join(to_list) if to_list else "Unknown",
            "from": from_email or "MyChama <noreply@mychama.app>",
            "subject": subject,
            "body": body,
            "html_body": html_body,
            "category": category,
            "otp_codes": otp_codes,
            "links": links,
        }


def get_dev_emails(
    recipient: str | None = None,
    category: str | None = None,
    limit: int = 10
) -> list[dict[str, Any]]:
    """Get stored development emails."""
    load_stored_emails()
    
    emails = _dev_emails.copy()
    
    # Filter by recipient
    if recipient:
        emails = [e for e in emails if recipient.lower() in e.get("to", "").lower()]
    
    # Filter by category
    if category:
        emails = [e for e in emails if e.get("category") == category]
    
    # Return most recent
    return emails[-limit:][::-1]


def get_latest_otp() -> str | None:
    """Get the most recent OTP code."""
    emails = get_dev_emails(category="otp", limit=5)
    
    for email in emails:
        codes = email.get("otp_codes", [])
        if codes:
            return codes[0]
    
    # Fallback to verification emails
    emails = get_dev_emails(category="verification", limit=5)
    for email in emails:
        codes = email.get("otp_codes", [])
        if codes:
            return codes[0]
    
    return None


def get_latest_invite_link() -> str | None:
    """Get the most recent invite link."""
    emails = get_dev_emails(category="invite", limit=5)
    
    for email in emails:
        links = email.get("links", [])
        for link in links:
            if link.get("type") == "invite":
                return link.get("url")
    
    return None


def get_latest_password_reset_link() -> str | None:
    """Get the most recent password reset link."""
    emails = get_dev_emails(category="password_reset", limit=5)
    
    for email in emails:
        links = email.get("links", [])
        for link in links:
            if link.get("type") == "password_reset":
                return link.get("url")
    
    return None


def clear_dev_emails() -> None:
    """Clear all stored development emails."""
    global _dev_emails
    
    with _dev_emails_lock:
        _dev_emails = []
    
    # Clear storage file
    if STORAGE_FILE.exists():
        STORAGE_FILE.unlink()
