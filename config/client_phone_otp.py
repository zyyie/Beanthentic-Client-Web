"""Phone OTP for client orders — SMS via SMS Gate (same as Beanthentic App)."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import beanthentic_env

_OTP_TTL_SEC = 5 * 60
_TOKEN_TTL_SEC = 30 * 60
_RESEND_SEC = 60
_MAX_ATTEMPTS = 5
_lock = threading.Lock()
_otp_by_phone: dict[str, dict] = {}
_token_by_value: dict[str, dict] = {}


def _load_sms_env() -> None:
    root = Path(__file__).resolve().parent.parent
    candidates = [root / "sms-gate.env"]
    parent = root.parent
    if parent.is_dir():
        for child in parent.iterdir():
            if not child.is_dir():
                continue
            candidates.append(child / "sms-gate.env")
            for nested in child.iterdir():
                if nested.is_dir():
                    candidates.append(nested / "sms-gate.env")
    for path in candidates:
        if path.is_file():
            beanthentic_env.load_dotenv(path)
            break


_load_sms_env()


def normalize_ph_mobile(raw: str) -> str:
    digits = re.sub(r"\D+", "", str(raw or ""))
    if digits.startswith("63") and len(digits) >= 12:
        digits = digits[2:]
    if len(digits) == 11 and digits.startswith("09"):
        digits = digits[1:]
    if len(digits) == 10 and digits.startswith("9"):
        return "0" + digits
    return ""


def phone_to_e164(phone: str) -> str:
    local = normalize_ph_mobile(phone)
    if local:
        return "+63" + local[1:]
    return ""


def mask_phone(phone: str) -> str:
    local = normalize_ph_mobile(phone)
    if len(local) < 7:
        return local or "****"
    return f"{local[:4]}***{local[-2:]}"


def _hash_code(phone: str, code: str) -> str:
    secret = (os.getenv("BEANTHENTIC_OTP_SECRET") or "beanthentic-client-otp").encode("utf-8")
    msg = f"{phone}:{code}".encode("utf-8")
    return hmac.new(secret, msg, hashlib.sha256).hexdigest()


def _send_sms_via_sms_gate(phone: str, message: str) -> dict:
    user = (os.getenv("SMS_GATE_USERNAME") or "").strip()
    password = os.getenv("SMS_GATE_PASSWORD") or ""
    if not user or not password:
        return {"ok": False, "error": "SMS is not configured."}

    to_num = phone_to_e164(phone)
    if not to_num:
        return {"ok": False, "error": "Invalid recipient phone number."}

    base_url = (os.getenv("SMS_GATE_BASE_URL") or "https://api.sms-gate.app/3rdparty/v1").strip().rstrip("/")
    url = f"{base_url}/messages?skipPhoneValidation=true"
    payload: dict = {
        "textMessage": {"text": message},
        "phoneNumbers": [to_num],
        "priority": 100,
        "ttl": 600,
        "withDeliveryReport": False,
    }
    device_id = (os.getenv("SMS_GATE_DEVICE_ID") or "").strip()
    if device_id:
        payload["deviceId"] = device_id
    sim_raw = (os.getenv("SMS_GATE_SIM_NUMBER") or "").strip()
    if sim_raw.isdigit():
        payload["simNumber"] = int(sim_raw)

    auth = base64.b64encode(f"{user}:{password}".encode("utf-8")).decode("ascii")
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Basic {auth}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            status = getattr(resp, "status", 200)
        data = json.loads(raw) if raw else None
        if 200 <= status < 300:
            if isinstance(data, dict) and data.get("error"):
                return {"ok": False, "error": str(data.get("message") or data.get("error") or "SMS gateway error.")}
            return {"ok": True}
        return {"ok": False, "error": "SMS gateway rejected the message."}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
        err = "SMS gateway rejected the message."
        try:
            data = json.loads(raw) if raw else None
            if isinstance(data, dict):
                err = str(data.get("message") or data.get("error") or err)
        except json.JSONDecodeError:
            pass
        return {"ok": False, "error": err}
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return {"ok": False, "error": "Could not reach SMS gateway. Check the SMS Gate app and network."}


def send_client_otp(phone: str) -> tuple[dict, int]:
    local = normalize_ph_mobile(phone)
    if not local:
        return {"ok": False, "error": "Enter a valid PH mobile number (09XXXXXXXXX)."}, 400

    now = time.time()
    with _lock:
        row = _otp_by_phone.get(local) or {}
        last_sent = float(row.get("last_sent") or 0)
        wait = int(_RESEND_SEC - (now - last_sent))
        if wait > 0:
            return {
                "ok": False,
                "error": f"Please wait {wait}s before requesting another code.",
                "retry_after": wait,
            }, 429
        code = f"{secrets.randbelow(1_000_000):06d}"
        _otp_by_phone[local] = {
            "hash": _hash_code(local, code),
            "expires": now + _OTP_TTL_SEC,
            "attempts": 0,
            "last_sent": now,
        }

    message = f"Beanthentic code: {code}. Valid for 5 minutes. Do not share this code."
    sent = _send_sms_via_sms_gate(local, message)
    if not sent.get("ok"):
        with _lock:
            _otp_by_phone.pop(local, None)
        return {"ok": False, "error": sent.get("error") or "Could not send verification SMS."}, 502

    return {
        "ok": True,
        "message": f"We sent a 6-digit code to {mask_phone(local)}.",
        "masked_phone": mask_phone(local),
        "retry_after": _RESEND_SEC,
    }, 200


def verify_client_otp(phone: str, code: str) -> tuple[dict, int]:
    local = normalize_ph_mobile(phone)
    entered = re.sub(r"\D+", "", str(code or ""))
    if not local:
        return {"ok": False, "error": "Enter a valid PH mobile number (09XXXXXXXXX)."}, 400
    if len(entered) != 6:
        return {"ok": False, "error": "Enter the 6-digit code we sent."}, 400

    now = time.time()
    with _lock:
        row = _otp_by_phone.get(local)
        if not row:
            return {"ok": False, "error": "Request a new verification code first."}, 400
        if now > float(row.get("expires") or 0):
            _otp_by_phone.pop(local, None)
            return {"ok": False, "error": "That code expired. Request a new one."}, 400
        attempts = int(row.get("attempts") or 0) + 1
        row["attempts"] = attempts
        if attempts > _MAX_ATTEMPTS:
            _otp_by_phone.pop(local, None)
            return {"ok": False, "error": "Too many attempts. Request a new code."}, 400
        expected = str(row.get("hash") or "")
        if not hmac.compare_digest(expected, _hash_code(local, entered)):
            return {"ok": False, "error": "Incorrect code. Please try again."}, 400
        _otp_by_phone.pop(local, None)
        token = secrets.token_urlsafe(24)
        _token_by_value[token] = {"phone": local, "expires": now + _TOKEN_TTL_SEC}

    return {
        "ok": True,
        "message": "Number verified.",
        "phone": local,
        "phone_verify_token": token,
    }, 200


def consume_phone_token(phone: str, token: str) -> str:
    local = normalize_ph_mobile(phone)
    raw = str(token or "").strip()
    if not local or not raw:
        return ""
    now = time.time()
    with _lock:
        row = _token_by_value.get(raw)
        if not row:
            return ""
        if now > float(row.get("expires") or 0):
            _token_by_value.pop(raw, None)
            return ""
        if str(row.get("phone") or "") != local:
            return ""
        return local
