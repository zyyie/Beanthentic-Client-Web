"""Generate downloadable QR images for the Beanthentic Client Web URL."""
from __future__ import annotations

import os
from pathlib import Path

import qrcode
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
QR_PLAIN_PATH = ROOT / "static" / "images" / "client-website-qr.png"
QR_PRINT_PATH = ROOT / "static" / "images" / "client-website-qr-print.png"


def _load_font(size: int) -> ImageFont.ImageFont:
    candidates = [
        "C:/Windows/Fonts/segoeuib.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
        "C:/Windows/Fonts/arial.ttf",
    ]
    for path in candidates:
        if Path(path).is_file():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def _read_tunnel_url_file() -> str:
    for name in ("tunnel-url.txt", "public-url.txt"):
        saved = ROOT / name
        if not saved.is_file():
            continue
        text = saved.read_text(encoding="utf-8").strip()
        if text:
            return text
    return ""


def resolve_client_web_url(explicit: str | None = None) -> str:
    custom = (explicit or "").strip()
    if not custom:
        # Active quick-tunnel URL wins over static domain in .env
        tunnel_live = _read_tunnel_url_file()
        if "trycloudflare.com" in tunnel_live:
            custom = tunnel_live
    if not custom:
        custom = (
            os.getenv("BEANTHENTIC_PUBLIC_URL")
            or os.getenv("BEANTHENTIC_CLIENT_WEB_URL")
            or ""
        ).strip()
    if not custom:
        host = os.getenv("BEANTHENTIC_CLOUDFLARE_HOSTNAME", "").strip()
        if host:
            custom = host
    if not custom:
        custom = _read_tunnel_url_file()
    if custom and not custom.startswith(("http://", "https://")):
        custom = f"https://{custom.strip('/')}/"
    if custom:
        return custom if custom.endswith("/") else custom + "/"
    port = os.getenv("BEANTHENTIC_PORT", "5001").strip() or "5001"
    try:
        from web import _get_wifi_ipv4_addresses

        ips = _get_wifi_ipv4_addresses()
        host = ips[0] if ips else "127.0.0.1"
    except Exception:
        host = "127.0.0.1"
    return f"http://{host}:{port}/"


def _build_qr_card(url: str, out_path: Path) -> None:
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=12,
        border=2,
    )
    qr.add_data(url)
    qr.make(fit=True)
    qr_img = qr.make_image(fill_color="#1e6216", back_color="#ffffff").convert("RGB")

    title = "Beanthentic Client Web"
    subtitle = "Scan to open the client website"
    title_font = _load_font(34)
    sub_font = _load_font(22)
    url_font = _load_font(20)

    pad_x = 40
    pad_top = 36
    pad_bottom = 40
    gap = 18

    tmp = Image.new("RGB", (1, 1), "#f4faf3")
    draw = ImageDraw.Draw(tmp)
    title_box = draw.textbbox((0, 0), title, font=title_font)
    sub_box = draw.textbbox((0, 0), subtitle, font=sub_font)
    url_box = draw.textbbox((0, 0), url, font=url_font)

    text_w = max(
        title_box[2] - title_box[0],
        sub_box[2] - sub_box[0],
        url_box[2] - url_box[0],
    )
    card_w = max(qr_img.width + pad_x * 2, text_w + pad_x * 2)
    card_h = (
        pad_top
        + (title_box[3] - title_box[1])
        + gap
        + (sub_box[3] - sub_box[1])
        + gap
        + qr_img.height
        + gap
        + (url_box[3] - url_box[1])
        + pad_bottom
    )

    card = Image.new("RGB", (card_w, card_h), "#f4faf3")
    draw = ImageDraw.Draw(card)

    y = pad_top
    draw.text(
        ((card_w - (title_box[2] - title_box[0])) / 2, y),
        title,
        fill="#1e6216",
        font=title_font,
    )
    y += title_box[3] - title_box[1] + gap
    draw.text(
        ((card_w - (sub_box[2] - sub_box[0])) / 2, y),
        subtitle,
        fill="#4a5a4c",
        font=sub_font,
    )
    y += sub_box[3] - sub_box[1] + gap
    card.paste(qr_img, ((card_w - qr_img.width) // 2, y))
    y += qr_img.height + gap
    draw.text(
        ((card_w - (url_box[2] - url_box[0])) / 2, y),
        url,
        fill="#1e6216",
        font=url_font,
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    card.save(out_path, format="PNG", optimize=True)


def ensure_client_qr_files(url: str | None = None) -> dict[str, Path]:
    target_url = resolve_client_web_url(url)
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=16,
        border=4,
    )
    qr.add_data(target_url)
    qr.make(fit=True)
    QR_PLAIN_PATH.parent.mkdir(parents=True, exist_ok=True)
    plain = qr.make_image(fill_color="#000000", back_color="#ffffff").convert("RGB")
    side = max(plain.size)
    canvas = Image.new("RGB", (side, side), "#ffffff")
    canvas.paste(plain, ((side - plain.width) // 2, (side - plain.height) // 2))
    canvas.save(QR_PLAIN_PATH, format="PNG", optimize=True)
    _build_qr_card(target_url, QR_PRINT_PATH)
    return {
        "plain": QR_PLAIN_PATH,
        "print": QR_PRINT_PATH,
        "target_url": target_url,
    }
