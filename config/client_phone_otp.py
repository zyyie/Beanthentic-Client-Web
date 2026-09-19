"""Phone OTP for client orders — SMS via SMS Gate (same as Beanthentic App)."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import ssl
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
    """Always (re)apply SMS Gate keys from sms-gate.env / .env so OTP works after restart."""
    root = Path(__file__).resolve().parent.parent
    paths = [root / "sms-gate.env", root / ".env"]
    for path in paths:
        if not path.is_file():
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for raw in lines:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key.startswith("SMS_GATE") or key == "BEANTHENTIC_OTP_SECRET":
                os.environ[key] = val
    os.environ.pop("SMS_GATE_SIM_NUMBER", None)


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


def _ssl_contexts():
    import ssl
    contexts = []
    try:
        import certifi
        contexts.append(ssl.create_default_context(cafile=certifi.where()))
    except Exception:
        pass
    try:
        contexts.append(ssl.create_default_context())
    except Exception:
        pass
    contexts.append(ssl._create_unverified_context())
    return contexts


def _urlopen_sms(req, timeout: int = 20):
    last_err: Exception | None = None
    for ctx in _ssl_contexts():
        try:
            return urllib.request.urlopen(req, timeout=timeout, context=ctx)
        except ssl.SSLError as exc:
            last_err = exc
            continue
        except urllib.error.URLError as exc:
            reason = str(getattr(exc, "reason", "") or exc)
            if "CERTIFICATE" in reason.upper() or "SSL" in reason.upper():
                last_err = exc
                continue
            raise
    if last_err:
        raise last_err
    raise urllib.error.URLError("Could not open SMS gateway connection.")


def _sms_gate_headers(user: str, password: str) -> dict:
    auth = base64.b64encode(f"{user}:{password}".encode("utf-8")).decode("ascii")
    return {
        "Authorization": f"Basic {auth}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        ),
    }


def _sms_gate_targets() -> list[dict]:
    """Local SMS Gate app first, then public cloud."""
    _load_sms_env()
    targets: list[dict] = []
    local_user = (os.getenv("SMS_GATE_LOCAL_USERNAME") or "").strip()
    local_pass = os.getenv("SMS_GATE_LOCAL_PASSWORD") or ""
    local_base = (os.getenv("SMS_GATE_LOCAL_BASE_URL") or "").strip().rstrip("/")
    local_device = (os.getenv("SMS_GATE_LOCAL_DEVICE_ID") or "").strip()
    if local_user and local_pass and local_base:
        targets.append({
            "base_url": local_base,
            "user": local_user,
            "password": local_pass,
            "device_id": local_device,
            "send_path": "message",
        })
    cloud_user = (os.getenv("SMS_GATE_USERNAME") or "").strip()
    cloud_pass = os.getenv("SMS_GATE_PASSWORD") or ""
    cloud_base = (os.getenv("SMS_GATE_BASE_URL") or "https://api.sms-gate.app/3rdparty/v1").strip().rstrip("/")
    cloud_device = (os.getenv("SMS_GATE_DEVICE_ID") or "").strip()
    if cloud_user and cloud_pass:
        targets.append({
            "base_url": cloud_base,
            "user": cloud_user,
            "password": cloud_pass,
            "device_id": cloud_device,
            "send_path": "messages",
        })
    return targets


def _humanize_sms_error(raw: str) -> str:
    text = str(raw or "").strip()
    upper = text.upper()
    if "NO_SERVICE" in upper:
        return (
            "SMS Gate received the OTP, but the gateway phone has no cellular service "
            "(no signal / SIM off). Turn on Mobile network on that phone — mobile data "
            "can stay off — keep SMS Gate ONLINE, then tap Send again."
        )
    if "RADIO_OFF" in upper:
        return "Turn off Airplane mode on the SMS Gate phone, then tap Send again."
    if "NO_SIM" in upper or "SIM" in upper and "NOT FOUND" in upper:
        return "SMS Gate cannot use that SIM slot. Sending with the phone's default SIM instead."
    return text or "Could not send verification SMS."


def _message_state_error(data: dict) -> str:
    recipients = data.get("recipients") if isinstance(data, dict) else None
    if isinstance(recipients, list):
        for row in recipients:
            if isinstance(row, dict) and row.get("error"):
                return _humanize_sms_error(str(row.get("error")))
    return _humanize_sms_error(str((data or {}).get("reason") or "SMS sending failed."))


def _get_json(url: str, headers: dict) -> tuple[int, object]:
    req = urllib.request.Request(url, headers=headers, method="GET")
    with _urlopen_sms(req, timeout=12) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
        status = getattr(resp, "status", 200)
    if not raw:
        return status, None
    try:
        return status, json.loads(raw)
    except json.JSONDecodeError:
        return status, None


def _poll_sms_state(target: dict, msg_id: str) -> dict:
    headers = _sms_gate_headers(target["user"], target["password"])
    base = str(target.get("base_url") or "").rstrip("/")
    paths = [f"{base}/messages/{msg_id}", f"{base}/message/{msg_id}"]
    last_state = "Pending"
    last_data: dict = {}
    for _ in range(7):
        time.sleep(1)
        for url in paths:
            try:
                status, data = _get_json(url, headers)
            except Exception:
                continue
            if not (200 <= status < 300) or not isinstance(data, dict):
                continue
            last_data = data
            last_state = str(data.get("state") or last_state)
            if last_state in ("Sent", "Delivered"):
                return {"ok": True}
            if last_state == "Failed":
                return {"ok": False, "error": _message_state_error(data)}
            break
    if last_state in ("Pending", "Processed"):
        return {
            "ok": False,
            "error": (
                "OTP is queued in SMS Gate but not sent yet. Keep the SMS Gate app open "
                "and ONLINE, then tap Send again in a few seconds."
            ),
        }
    if last_state == "Failed":
        return {"ok": False, "error": _message_state_error(last_data)}
    return {"ok": True}


def _post_sms_gate_message(target: dict, to_num: str, message: str, sim_number: int | None = None) -> dict:
    send_path = str(target.get("send_path") or "messages").strip("/")
    url = f"{target['base_url'].rstrip('/')}/{send_path}?skipPhoneValidation=true&deviceActiveWithin=12"
    payload: dict = {
        "textMessage": {"text": message},
        "phoneNumbers": [to_num],
        "priority": 100,
        "ttl": 3600,
        "withDeliveryReport": True,
    }
    device_id = str(target.get("device_id") or "").strip()
    if device_id:
        payload["deviceId"] = device_id
    if sim_number in (1, 2, 3):
        payload["simNumber"] = sim_number

    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers=_sms_gate_headers(target["user"], target["password"]),
    )
    try:
        with _urlopen_sms(req, timeout=20) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            status = getattr(resp, "status", 200)
        data = json.loads(raw) if raw else None
        if not (200 <= status < 300):
            return {"ok": False, "error": "SMS gateway rejected the message."}
        if isinstance(data, dict) and data.get("error"):
            return {"ok": False, "error": _humanize_sms_error(str(data.get("message") or data.get("error")))}
        msg_id = ""
        if isinstance(data, dict):
            msg_id = str(data.get("id") or "").strip()
            state = str(data.get("state") or "")
            if state == "Failed":
                return {"ok": False, "error": _message_state_error(data)}
        if msg_id:
            polled = _poll_sms_state(target, msg_id)
            if polled.get("ok"):
                return {"ok": True}
            return {
                "ok": True,
                "queued": True,
                "warning": polled.get("error") or "",
            }
        return {"ok": True}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
        err = "SMS gateway rejected the message."
        try:
            parsed = json.loads(raw) if raw else None
            if isinstance(parsed, dict):
                err = str(parsed.get("message") or parsed.get("error") or err)
        except json.JSONDecodeError:
            pass
        return {"ok": False, "error": _humanize_sms_error(err)}
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return {"ok": False, "error": "Could not reach SMS gateway. Check the SMS Gate app and network."}


def _send_sms_via_sms_gate(phone: str, message: str) -> dict:
    to_num = phone_to_e164(phone)
    if not to_num:
        return {"ok": False, "error": "Invalid recipient phone number."}

    targets = _sms_gate_targets()
    if not targets:
        return {"ok": False, "error": "SMS is not configured."}

    last = {"ok": False, "error": "Could not send verification SMS."}
    for target in targets:
        last = _post_sms_gate_message(target, to_num, message)
        if last.get("ok"):
            return last
        err = str(last.get("error") or "").lower()
        if "sim" in err and "not found" in err:
            for slot in (2, 3):
                last = _post_sms_gate_message(target, to_num, message, sim_number=slot)
                if last.get("ok"):
                    return last
            continue
        if "no cellular service" in err or "airplane" in err:
            return last
    return last


def send_client_otp(phone: str) -> tuple[dict, int]:
    _load_sms_env()
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
    if not sent.get("ok") and not sent.get("queued"):
        with _lock:
            _otp_by_phone.pop(local, None)
        return {"ok": False, "error": sent.get("error") or "Could not send verification SMS."}, 502

    warning = str(sent.get("warning") or "").strip()
    msg = f"We sent a 6-digit code to {mask_phone(local)}. Enter it below to verify."
    if warning:
        msg = (
            f"A 6-digit code was created for {mask_phone(local)}. "
            "Enter the code from your SMS (or SMS Gate Sent folder), then tap Verify."
        )
    return {
        "ok": True,
        "message": msg,
        "masked_phone": mask_phone(local),
        "retry_after": _RESEND_SEC,
        "warning": warning,
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
