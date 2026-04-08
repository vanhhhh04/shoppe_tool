"""
Luồng gộp ViOTP → Shopee (Playwright như ``a.py``) → KiotProxy xoay theo ``ttl``.

1. Thuê ``SIM_COUNT`` số Việt Nam, dịch vụ Shopee (tham số cấu hình sẵn / env).
2. Chuẩn hóa số sang dạng Shopee (84…).
3. Mở browser, kiểm tra ``check_account_exist`` qua UI (``ShopeeBrowserClient``).
4. Proxy Kiot: sau ``ensure_valid()`` đọc ``ttl`` (giây) từ API → ``kiot_rotate_after_seconds``
   để đổi IP theo chu kỳ trùng tuổi thọ gói; có thể ghi đè bằng env ``KIOT_ROTATE_AFTER_SECONDS``.

Chạy: ``python viotp_shopee_kiot_flow.py`` (cần ``.env``: ``viotp_token``, Kiot proxy key + bearer).
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from dotenv import load_dotenv

from a import (
    SHOPEE_ERROR_RISK_GENERIC,
    ShopeeBrowserClient,
    ShopeeUiTiming,
    kiot_key_from_environ,
    kiot_proxy_from_env,
)
from kiotproxy_api import Proxy
from viotp_api import ViOTPClient

# --- ViOTP: tham số mặc định (ghi đè bằng biến môi trường nếu cần) ---
SIM_COUNT = int(os.environ.get("VIOTP_SIM_COUNT", "5"))
VIOTP_COUNTRY = os.environ.get("VIOTP_COUNTRY", "vn").strip() or "vn"
# ViOTP service id Shopee — kiểm tra dashboard ViOTP; mặc định 4 (có thể đổi sang 2).
VIOTP_SHOPEE_SERVICE_ID = int(os.environ.get("VIOTP_SHOPEE_SERVICE_ID", "4"))
# Tuỳ chọn: nhà mạng / đầu số (để trống = không gửi)
VIOTP_NETWORK = os.environ.get("VIOTP_NETWORK", "").strip() or None
VIOTP_PREFIX = os.environ.get("VIOTP_PREFIX", "").strip() or None
VIOTP_EXCEPT_PREFIX = os.environ.get("VIOTP_EXCEPT_PREFIX", "").strip() or None


def normalize_phone_for_shopee(phone: str) -> str:
    """Shopee/UI thường dùng chuỗi số quốc gia 84, không 0 đầu."""
    digits = "".join(c for c in (phone or "") if c.isdigit())
    if not digits:
        return digits
    if digits.startswith("84"):
        return digits
    if digits.startswith("0"):
        return "84" + digits[1:]
    if len(digits) == 9:
        return "84" + digits
    return digits


@dataclass
class RentedLine:
    phone_raw: str
    phone_shopee: str
    request_id: str
    viotp_response: Dict[str, Any]


def rent_viotp_batch(
    client: ViOTPClient,
    *,
    count: int,
    service_id: int,
    country: str,
    network: Optional[str] = None,
    prefix: Optional[str] = None,
    except_prefix: Optional[str] = None,
    pause_between_s: float = 0.35,
) -> Tuple[List[RentedLine], List[Dict[str, Any]]]:
    """
    Thuê ``count`` số. Trả về danh sách thành công và log các lần gọi thất bại.
    """
    rented: List[RentedLine] = []
    failures: List[Dict[str, Any]] = []
    for _ in range(count):
        resp = client.request_number(
            service_id=service_id,
            network=network,
            prefix=prefix,
            except_prefix=except_prefix,
            country=country,
        )
        if resp.get("status_code") != 200:
            failures.append(resp)
            break
        data = resp.get("data") or {}
        phone = data.get("phone_number")
        req_id = data.get("request_id")
        if not phone or not req_id:
            failures.append(resp)
            break
        rented.append(
            RentedLine(
                phone_raw=str(phone),
                phone_shopee=normalize_phone_for_shopee(str(phone)),
                request_id=str(req_id),
                viotp_response=resp,
            )
        )
        if pause_between_s > 0:
            time.sleep(pause_between_s)
    return rented, failures


def kiot_rotate_interval_seconds(kiot: Proxy) -> float:
    """
    Chu kỳ xoay proxy (giây) theo ``ttl`` từ API sau ``ensure_valid()``;
    fallback ``KIOT_ROTATE_AFTER_SECONDS``; 0 = logic thông minh trong ``a.py`` (ttc/nextRequestAt).
    """
    env_override = float(os.environ.get("KIOT_ROTATE_AFTER_SECONDS", "0") or 0)
    if env_override > 0:
        return env_override
    raw = kiot.raw
    if not raw:
        return 0.0
    ttl = raw.get("ttl")
    try:
        v = float(ttl) if ttl is not None else 0.0
    except (TypeError, ValueError):
        v = 0.0
    return max(0.0, v)


def run_flow(
    *,
    sim_count: int = SIM_COUNT,
    headless: bool = True,
    use_system_chrome: bool = False,
    fast_timing: bool = False,
    debug_dir: Optional[Path] = None,
    kiot_region: Optional[str] = None,
) -> Dict[str, Any]:
    load_dotenv(Path(__file__).resolve().parent / ".env", override=True)

    viotp_token = (os.environ.get("viotp_token") or "").strip()
    if not viotp_token:
        raise RuntimeError("Thiếu viotp_token trong .env")

    kiot_key = kiot_key_from_environ()
    if not kiot_key:
        raise RuntimeError(
            "Thiếu Kiot Proxy Key: đặt KIOTPROXY_PROXY_KEY (menu Key) trong .env",
        )

    region = kiot_region if kiot_region is not None else os.environ.get("KIOTPROXY_REGION", "random")
    kiot = Proxy(key=kiot_key.strip(), region=region)
    kiot.ensure_valid()
    rotate_after = kiot_rotate_interval_seconds(kiot)

    vi = ViOTPClient(token=viotp_token)
    rented, failures = rent_viotp_batch(
        vi,
        count=sim_count,
        service_id=VIOTP_SHOPEE_SERVICE_ID,
        country=VIOTP_COUNTRY,
        network=VIOTP_NETWORK,
        prefix=VIOTP_PREFIX,
        except_prefix=VIOTP_EXCEPT_PREFIX,
    )
    if not rented:
        return {
            "ok": False,
            "error": "Không thuê được số ViOTP nào",
            "viotp_failures": failures,
            "kiot_rotate_after_seconds": rotate_after,
        }

    phones = [r.phone_shopee for r in rented]
    timing = ShopeeUiTiming.fast() if fast_timing else ShopeeUiTiming()

    start = time.time()
    with ShopeeBrowserClient(
        headless=headless,
        use_system_chrome=use_system_chrome,
        kiot_proxy=kiot,
        rotate_kiot_each_session=True,
        kiot_rotate_after_seconds=rotate_after,
        debug_dir=debug_dir,
        timing=timing,
    ) as browser_client:
        results = browser_client.check_multiple_accounts(phones, use_ui=True)
    elapsed = time.time() - start

    return {
        "ok": True,
        "sim_count_requested": sim_count,
        "sim_count_rented": len(rented),
        "viotp_service_id": VIOTP_SHOPEE_SERVICE_ID,
        "viotp_country": VIOTP_COUNTRY,
        "kiot_rotate_after_seconds": rotate_after,
        "rented": [r.__dict__ for r in rented],
        "viotp_failures_after_batch": failures,
        "shopee_results": [{"phone": p, "response": r} for p, r in results],
        "elapsed_seconds": round(elapsed, 2),
    }


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--headed", action="store_true", help="Hiện cửa sổ trình duyệt")
    parser.add_argument("--chrome", action="store_true", help="Dùng Chrome hệ thống")
    parser.add_argument("--fast", action="store_true", help="Timeout/sleep ngắn hơn")
    parser.add_argument("--count", type=int, default=SIM_COUNT, help=f"Số SIM ViOTP (mặc định {SIM_COUNT})")
    parser.add_argument("--debug-dir", default=None, metavar="DIR", help="Screenshot khi lỗi UI")
    parser.add_argument(
        "--kiot-region",
        default=os.environ.get("KIOTPROXY_REGION", "random"),
        help="bac | trung | nam | random",
    )
    args = parser.parse_args()
    dd = Path(args.debug_dir) if args.debug_dir else None
    out = run_flow(
        sim_count=args.count,
        headless=not args.headed,
        use_system_chrome=args.chrome,
        fast_timing=args.fast,
        debug_dir=dd,
        kiot_region=args.kiot_region,
    )
    print(json.dumps(out, ensure_ascii=False, indent=2))
    print(
        f"\nNếu Shopee trả error={SHOPEE_ERROR_RISK_GENERIC}: thường là risk — thử proxy/VPN khác.",
    )
    raise SystemExit(0 if out.get("ok") else 1)
