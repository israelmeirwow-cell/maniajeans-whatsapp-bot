"""Central configuration. Loads from environment, with a simple .env fallback.

Secrets are never hardcoded. For the demo on this machine, ANTHROPIC_API_KEY is
read from (in order): process env -> whatsapp_agent/.env -> ../../.env (chatbot/.env).
"""
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent          # .../whatsapp_agent
DATA_DIR = PROJECT_ROOT / "data"
STORE_DIR = DATA_DIR / "store"


def store_file(name: str) -> Path:
    """Resolve a store data file, falling back to its committed `.sample` twin.

    The real scraped catalog stays local + git-ignored; the public repo ships only
    synthetic `*.sample.*` demo data. On the owner's machine the real file wins; on a
    fresh clone only the sample exists, so the demo runs out of the box either way.
    """
    real = STORE_DIR / name
    if real.is_file():
        return real
    stem, dot, ext = name.partition(".")
    return STORE_DIR / f"{stem}.sample{dot}{ext}"


def _load_env_file(path: Path) -> None:
    """Minimal .env loader (no external dependency). Does not override existing env."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val


# Load .env fallbacks (project first, then the parent chatbot/.env for the API key)
_load_env_file(PROJECT_ROOT / ".env")
_load_env_file(PROJECT_ROOT.parent.parent / ".env")   # /Users/israelmeir/chatbot/.env


class Settings:
    # --- Claude ---
    ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
    CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-5")
    CLAUDE_CLASSIFIER_MODEL = os.environ.get("CLAUDE_CLASSIFIER_MODEL", "claude-haiku-4-5-20251001")

    # --- WhatsApp gateway (Evolution API) — used in later milestones ---
    WA_GATEWAY_URL = os.environ.get("WA_GATEWAY_URL", "")
    WA_GATEWAY_API_KEY = os.environ.get("WA_GATEWAY_API_KEY", "")
    WA_INSTANCE_NAME = os.environ.get("WA_INSTANCE_NAME", "")
    WA_WEBHOOK_SECRET = os.environ.get("WA_WEBHOOK_SECRET", "")

    # --- Business rules ---
    # An order past its ETA (and not delivered/ready) escalates to a human (intent #1).
    DELIVERY_SLA_DAYS = int(os.environ.get("DELIVERY_SLA_DAYS", "10"))
    HANDOFF_NOTIFY = os.environ.get("HANDOFF_NOTIFY", "")   # phone/email/channel for a human agent

    # --- Store / brand ---
    STORE_NAME = os.environ.get("STORE_NAME", "DEMO Store")

    # --- Weekly-report economics (all overridable; the report states these assumptions) ---
    USD_TO_ILS = float(os.environ.get("USD_TO_ILS", "3.7"))       # for showing costs in ₪
    COST_PER_HUMAN_CONTACT_ILS = float(os.environ.get("COST_PER_HUMAN_CONTACT_ILS", "12"))
    AVG_ORDER_VALUE_ILS = float(os.environ.get("AVG_ORDER_VALUE_ILS", "220"))  # ~catalog avg
    # rough share of product-interest chats that convert to a sale (industry-typical assist rate)
    ASSUMED_CONVERSION = float(os.environ.get("ASSUMED_CONVERSION", "0.08"))

    # --- Email delivery (weekly report). For Gmail use an App Password, not your login. ---
    SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
    SMTP_USER = os.environ.get("SMTP_USER", "")
    SMTP_PASS = os.environ.get("SMTP_PASS", "")
    REPORT_EMAIL_TO = os.environ.get("REPORT_EMAIL_TO", "")

    # --- WhatsApp delivery of the weekly report (owner's number; sent once M1 gateway is live) ---
    REPORT_WHATSAPP_TO = os.environ.get("REPORT_WHATSAPP_TO", "")

    @property
    def has_claude(self) -> bool:
        return bool(self.ANTHROPIC_API_KEY)

    @property
    def has_smtp(self) -> bool:
        return bool(self.SMTP_USER and self.SMTP_PASS and self.REPORT_EMAIL_TO)


settings = Settings()
