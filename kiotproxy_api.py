"""
Client cho KiotProxy REST API (theo tài liệu chính thức).

- ``GET .../proxies/new?key=...&region=...`` — lấy / đổi proxy
- ``GET .../proxies/current?key=...`` — proxy đang gán cho key
- ``GET .../proxies/out?key=...`` — thoát proxy khỏi key

Tham số query ``key`` là **Proxy Key** (menu sidebar **Key** — key gắn với gói proxy),
**không phải** **API Token** (trang Cài đặt / Bảo mật → **API Token**). Hai loại khác nhau;
dán Token 32 ký tự vào ``?key=`` sẽ luôn ``KEY_NOT_FOUND``.

Trong ``.env``:

- ``KIOTPROXY_PROXY_KEY`` — **Proxy Key** (sidebar **Key**), tham số ``?key=``.
- ``KIOTPROXY_API_TOKEN`` hoặc ``kiotproxy_token`` — **API Token** (Cài đặt → API Token),
  gửi kèm header ``Authorization: Bearer …`` nếu API yêu cầu xác thực tài khoản.

Hai giá trị thường **khác nhau**; chỉ dán Token vào ``?key=`` vẫn ``KEY_NOT_FOUND``.
"""

import os
import threading
import time
import requests
from typing import Any, Dict, Optional

KIOT_BASE = "https://api.kiotproxy.com/api/v1/proxies"

# Vùng theo tài liệu KiotProxy: bac | trung | nam | random
VALID_REGIONS = frozenset({"bac", "trung", "nam", "random"})


def _api_token_from_environ() -> Optional[str]:
    """API Token (Bearer). Ưu tiên ``KIOTPROXY_API_TOKEN``, sau đó ``kiotproxy_token``."""
    t = os.environ.get("KIOTPROXY_API_TOKEN", "").strip()
    if t:
        return t
    t = os.environ.get("kiotproxy_token", "").strip()
    return t or None


def _proxy_key_from_environ() -> str:
    """
    Proxy Key từ env. Nếu **có** biến ``KIOTPROXY_PROXY_KEY`` (kể cả ``=`` rỗng) thì **chỉ**
    dùng giá trị đó — không fallback sang ``kiotproxy_token`` (tránh lỗi tưởng đã đổi key).
    """
    if "KIOTPROXY_PROXY_KEY" in os.environ:
        return os.environ["KIOTPROXY_PROXY_KEY"].strip()
    return (
        os.environ.get("kiotproxy_token", "").strip()
        or os.environ.get("KIOTPROXY_KEY", "").strip()
    )


def _preview_secret(s: Optional[str]) -> Optional[str]:
    if not s:
        return None
    if len(s) <= 8:
        return "(ngắn)"
    return f"{s[:4]}...{s[-4:]}"


def _api_fail_detail(payload: Dict[str, Any]) -> str:
    parts = [
        payload.get("message"),
        payload.get("error"),
    ]
    code = payload.get("code")
    if code is not None:
        parts.append(f"code={code}")
    return " — ".join(str(p) for p in parts if p not in (None, "")) or str(payload)


# Phản hồi tạm thời khi Kiot phân bổ tài nguyên — nên chờ rồi gọi lại GET .../new
_TRANSIENT_FETCH_NEW_ERRORS = frozenset(
    {
        "SYSTEM_IS_HAVING_TROUBLE_ALLOCATING_RESOURCES",
    }
)
_TRANSIENT_FETCH_NEW_CODES = frozenset({40001178})


def _is_transient_fetch_new_failure(payload: Dict[str, Any]) -> bool:
    err = payload.get("error")
    if err in _TRANSIENT_FETCH_NEW_ERRORS:
        return True
    code = payload.get("code")
    if code is None:
        return False
    try:
        return int(code) in _TRANSIENT_FETCH_NEW_CODES
    except (TypeError, ValueError):
        return False


class Proxy:
    """
    Quản lý proxy KiotProxy: lấy proxy mới/current, tự làm mới trước khi hết hạn,
    và trả dict tương thích với tham số ``proxies`` của requests.

    ``key``: giá trị gửi lên API là ``?key=...`` (Proxy Key từ Kiot).
    """

    def __init__(
        self,
        key: str,
        region: str = "random",
        timeout: float = 30.0,
        api_token: Optional[str] = None,
    ):
        k = (key or "").strip()
        if not k:
            raise ValueError(
                "KiotProxy: thiếu Proxy Key (GET ?key=...). Lấy từ menu Key trong dashboard, "
                "không dùng API Token (Cài đặt → API Token). .env: KIOTPROXY_PROXY_KEY=..."
            )
        self.key = k
        self.region = region if region in VALID_REGIONS else "random"
        self.timeout = timeout
        if api_token is not None:
            self._api_token: Optional[str] = api_token.strip() or None
        else:
            self._api_token = _api_token_from_environ()
        self._lock = threading.Lock()
        self._data: Optional[Dict[str, Any]] = None

    def _now_ms(self) -> int:
        return int(time.time() * 1000)

    def _request(self, path: str, extra_params: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        params: Dict[str, str] = {"key": self.key}
        if extra_params:
            params.update(extra_params)
        url = f"{KIOT_BASE}/{path}"
        headers: Dict[str, str] = {}
        if self._api_token:
            headers["Authorization"] = f"Bearer {self._api_token}"
        r = requests.get(url, params=params, headers=headers, timeout=self.timeout)
        try:
            return r.json()
        except ValueError:
            return {"success": False, "message": r.text, "raw_status": r.status_code}

    def fetch_new(self) -> Dict[str, Any]:
        return self._request("new", {"region": self.region})

    def fetch_current(self) -> Dict[str, Any]:
        return self._request("current")

    def release(self) -> Dict[str, Any]:
        with self._lock:
            resp = self._request("out")
            self._data = None
            return resp

    def _wait_until_next_request(self) -> None:
        """Chờ tới nextRequestAt (ms) nếu API yêu cầu chưa được gọi new."""
        next_at = None
        if self._data and isinstance(self._data, dict):
            next_at = self._data.get("nextRequestAt")
        if next_at is None:
            return
        now = self._now_ms()
        if now >= next_at:
            return
        delay = (next_at - now) / 1000.0 + 0.25
        if delay > 0:
            time.sleep(delay)

    def _store_from_response(self, payload: Dict[str, Any]) -> bool:
        if not payload.get("success"):
            return False
        data = payload.get("data")
        if not isinstance(data, dict) or not data.get("http"):
            return False
        self._data = data
        return True

    def ensure_valid(self, margin_ms: int = 15_000) -> None:
        """
        Đảm bảo proxy còn dùng được: giữ cache nếu chưa gần expirationAt;
        thử current rồi new nếu cần. margin_ms: làm mới sớm trước khi hết hạn.
        """
        with self._lock:
            now = self._now_ms()
            if self._data:
                exp = self._data.get("expirationAt")
                if exp is not None and now < exp - margin_ms:
                    return

            cur = self.fetch_current()
            if self._store_from_response(cur):
                exp = self._data.get("expirationAt") if self._data else None
                if exp is not None and now < exp - margin_ms:
                    return

            self._wait_until_next_request()
            new_resp: Dict[str, Any] = {}
            got_stored = False
            for attempt in range(6):
                new_resp = self.fetch_new()
                if self._store_from_response(new_resp):
                    got_stored = True
                    break
                if not _is_transient_fetch_new_failure(new_resp):
                    break
                if attempt < 5:
                    time.sleep(min(2.0 * (2**attempt), 45.0))
            if not got_stored:
                detail = _api_fail_detail(new_resp)
                hint = ""
                if new_resp.get("error") == "KEY_NOT_FOUND":
                    hint = (
                        " Khác **API Token** (Cài đặt → API Token): cần **Proxy Key** (sidebar menu **Key**). "
                        "Đặt KIOTPROXY_PROXY_KEY trong .env. Chạy: python kiotproxy_api.py"
                    )
                elif _is_transient_fetch_new_failure(new_resp):
                    hint = " (đã thử lại nhiều lần; chờ vài phút, đổi region, hoặc giảm tần suất /proxies/out + /new)."
                raise RuntimeError(f"KiotProxy: không lấy được proxy — {detail}.{hint}")

    def as_requests_proxies(self) -> Dict[str, str]:
        """Định dạng cho requests: http/https qua cùng endpoint HTTP proxy."""
        if not self._data or not self._data.get("http"):
            raise RuntimeError("Chưa có proxy; gọi ensure_valid() trước.")
        host = self._data["http"].strip()
        if host.lower().startswith("http://"):
            url = host
        else:
            url = f"http://{host}"
        return {"http": url, "https": url}

    def as_playwright_proxy(self) -> Dict[str, str]:
        """Tham số ``proxy`` cho Playwright ``browser.new_context(proxy=...)``."""
        http_url = self.as_requests_proxies()["http"]
        return {"server": http_url}

    def prepare_new_session(self) -> None:
        """
        Một session / một số = một IP: thoát proxy gắn key (nếu đang có) rồi lấy endpoint mới.

        Gọi trước mỗi ``new_context`` khi không muốn tái sử dụng cùng IP cho nhiều số.
        """
        released = False
        with self._lock:
            if self._data is not None:
                self._request("out")
                self._data = None
                released = True
        if released:
            time.sleep(0.45)
        self.ensure_valid()

    @property
    def raw(self) -> Optional[Dict[str, Any]]:
        """Bản sao thông tin proxy hiện tại (theo API), hoặc None."""
        if not self._data:
            return None
        return dict(self._data)


def get_new_proxy_response(
    key: str,
    region: str = "random",
    *,
    api_token: Optional[str] = None,
    timeout: float = 30.0,
) -> Dict[str, Any]:
    """Gọi thô ``GET .../proxies/new`` — dùng để kiểm tra key (``python kiotproxy_api.py``)."""
    reg = region if region in VALID_REGIONS else "random"
    tok = (api_token.strip() if api_token else None) or _api_token_from_environ()
    headers: Dict[str, str] = {}
    if tok:
        headers["Authorization"] = f"Bearer {tok}"
    r = requests.get(
        f"{KIOT_BASE}/new",
        params={"key": (key or "").strip(), "region": reg},
        headers=headers,
        timeout=timeout,
    )
    try:
        body = r.json()
    except ValueError:
        return {"success": False, "message": r.text, "raw_status": r.status_code}
    if isinstance(body, dict):
        return body
    return {"success": False, "_raw": body}


if __name__ == "__main__":
    import argparse
    import json
    import sys
    from pathlib import Path

    from dotenv import load_dotenv

    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except (OSError, ValueError, AttributeError):
            pass

    load_dotenv(Path(__file__).resolve().parent / ".env", override=True)
    parser = argparse.ArgumentParser(description="Thử KiotProxy GET /api/v1/proxies/new (kiểm tra Proxy Key).")
    parser.add_argument(
        "--key",
        default="",
        metavar="KEY",
        help="Proxy Key (menu Key); không phải API Token. Mặc định: env KIOTPROXY_PROXY_KEY…",
    )
    parser.add_argument("--region", default=os.environ.get("KIOTPROXY_REGION", "random"))
    cli = parser.parse_args()
    k = (cli.key or "").strip() or _proxy_key_from_environ()
    if not k:
        if "KIOTPROXY_PROXY_KEY" in os.environ and not os.environ.get("KIOTPROXY_PROXY_KEY", "").strip():
            parser.error(
                "KIOTPROXY_PROXY_KEY trong .env đang trống — mở dashboard Kiot → menu Key, "
                "copy Proxy Key (khác API Token) rồi dán sau dấu =."
            )
        parser.error("Thiếu Proxy Key: --key hoặc KIOTPROXY_PROXY_KEY / kiotproxy_token trong .env")
    preview = f"{k[:4]}...{k[-4:]}" if len(k) > 8 else "(ngắn)"
    print(json.dumps({"key_length": len(k), "key_preview": preview}, ensure_ascii=False))
    api_tok = _api_token_from_environ()
    print(
        json.dumps(
            {
                "api_bearer_configured": bool(api_tok),
                "api_bearer_preview": _preview_secret(api_tok),
            },
            ensure_ascii=False,
        )
    )
    resp = get_new_proxy_response(k, cli.region)
    print(json.dumps(resp, ensure_ascii=False, indent=2))
    if not resp.get("success") and resp.get("error") == "KEY_NOT_FOUND":
        hex32 = len(k) == 32 and all(c in "0123456789abcdefABCDEF" for c in k)
        if hex32:
            print(
                "\n(Lưu ý) Chuỗi 32 hex này gần như chắc là **API Token**, không phải **Proxy Key**. "
                "`?key=` bắt buộc là Proxy Key (sidebar menu **Key** — chuỗi thường khác hẳn).",
                file=sys.stderr,
            )
        if not api_tok:
            print(
                "\n`.env` thiếu **API Token** cho Bearer: thêm một trong hai dòng:\n"
                "  kiotproxy_token=<API Token từ Cài đặt → API Token>\n"
                "  hoặc KIOTPROXY_API_TOKEN=<cùng giá trị>\n"
                "Sau đó `KIOTPROXY_PROXY_KEY=<Proxy Key từ menu Key>` phải khác API Token.",
                file=sys.stderr,
            )
        elif hex32:
            print(
                "\nBạn đã có Bearer nhưng `key=` vẫn là 32 hex — cần **Proxy Key riêng** từ menu Key.",
                file=sys.stderr,
            )
        if "KIOTPROXY_PROXY_KEY" in os.environ and not os.environ["KIOTPROXY_PROXY_KEY"].strip():
            print(
                "\nKIOTPROXY_PROXY_KEY đang rỗng — xóa dòng đó hoặc dán Proxy Key thật vào sau dấu =.",
                file=sys.stderr,
            )
    raise SystemExit(0 if resp.get("success") else 1)
