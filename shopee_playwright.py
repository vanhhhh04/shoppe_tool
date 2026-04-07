"""
Gọi ``check_account_exist`` trong ngữ cảnh trình duyệt Chromium (Playwright).

Dùng ``fetch`` trong trang Shopee với ``credentials: 'include'`` để cookie / CSRF
và các layer JS gắn với phiên được áp dụng giống người dùng thật hơn so với ``requests``.

Cài đặt:
    pip install playwright playwright-stealth
    playwright install chromium

Lưu ý: tuân thủ điều khoản Shopee; vẫn có thể bị captcha / verify / chặn IP.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from playwright.sync_api import Browser, Page, Playwright, sync_playwright
from playwright_stealth import Stealth

RESET_URL = "https://shopee.vn/buyer/reset?scenario=7"
CHECK_URL = "https://shopee.vn/api/v4/account/basic/check_account_exist"

_CHROME_WIN_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
_SHOPEE_STEALTH = Stealth(
    navigator_languages_override=("vi-VN", "vi"),
    navigator_user_agent_override=_CHROME_WIN_UA,
    navigator_platform_override="Win32",
)


def _check_account_exist_in_page(page: Page, phone: str) -> Dict[str, Any]:
    """Chạy POST API trong JS context của trang (cùng origin, cookie tự đính kèm)."""
    return page.evaluate(
        """async ({ url, phone }) => {
            const r = await fetch(url, {
                method: "POST",
                credentials: "include",
                headers: {
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "X-Requested-With": "XMLHttpRequest",
                },
                body: JSON.stringify({ phone, scenario: 2 }),
            });
            const text = await r.text();
            let data;
            try {
                data = JSON.parse(text);
            } catch (e) {
                data = { _parse_error: String(e), _raw: text.slice(0, 2000) };
            }
            return {
                http_status: r.status,
                ok: r.ok,
                data,
            };
        }""",
        {"url": CHECK_URL, "phone": phone},
    )


class ShopeePlaywrightClient:
    """
    Mở Chromium, tải trang reset (session + JS), rồi gọi ``check_account_exist``.
    """

    def __init__(
        self,
        *,
        headless: bool = True,
        slow_mo_ms: int = 0,
        channel: Optional[str] = None,
    ):
        self.headless = headless
        self.slow_mo_ms = slow_mo_ms
        self.channel = channel  # ví dụ "chrome" để dùng Chrome đã cài

    def check_account_exist(
        self,
        phone: str,
        *,
        wait_until: str = "load",
        navigation_timeout_ms: int = 45_000,
    ) -> Dict[str, Any]:
        """
        :param phone: Số điện thoại (chuỗi digits, ví dụ ``84349575012``).
        :param wait_until: ``load`` | ``domcontentloaded`` | ``networkidle`` (networkidle có thể rất chậm trên Shopee).
        :return: ``{ "http_status", "ok", "data" }`` từ fetch; hoặc ``{ "error": ... }`` nếu lỗi Playwright.
        """
        with sync_playwright() as p:
            return self._run_with_playwright(
                p,
                phone,
                wait_until=wait_until,
                navigation_timeout_ms=navigation_timeout_ms,
            )

    def _run_with_playwright(
        self,
        p: Playwright,
        phone: str,
        *,
        wait_until: str,
        navigation_timeout_ms: int,
    ) -> Dict[str, Any]:
        launch_kwargs: Dict[str, Any] = {
            "headless": self.headless,
        }
        if self.slow_mo_ms:
            launch_kwargs["slow_mo"] = self.slow_mo_ms
        if self.channel:
            launch_kwargs["channel"] = self.channel

        browser: Optional[Browser] = None
        try:
            browser = p.chromium.launch(**launch_kwargs)
            context = browser.new_context(
                viewport={"width": 1280, "height": 800},
                locale="vi-VN",
                timezone_id="Asia/Ho_Chi_Minh",
                user_agent=_CHROME_WIN_UA,
                extra_http_headers={"Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7"},
            )
            page = context.new_page()
            _SHOPEE_STEALTH.apply_stealth_sync(page)
            page.set_default_navigation_timeout(navigation_timeout_ms)
            page.goto(RESET_URL, wait_until=wait_until)
            result = _check_account_exist_in_page(page, phone)
            return {
                "success": True,
                "http_status": result.get("http_status"),
                "ok": result.get("ok"),
                "data": result.get("data"),
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "message": "Lỗi Playwright hoặc mạng khi gọi Shopee",
            }
        finally:
            if browser:
                browser.close()


def check_account_exist_playwright(
    phone: str,
    *,
    headless: bool = True,
    slow_mo_ms: int = 0,
    channel: Optional[str] = None,
    wait_until: str = "load",
) -> Dict[str, Any]:
    """Hàm tiện ích một lần gọi."""
    client = ShopeePlaywrightClient(
        headless=headless,
        slow_mo_ms=slow_mo_ms,
        channel=channel,
    )
    return client.check_account_exist(phone, wait_until=wait_until)


if __name__ == "__main__":
    import sys

    p = sys.argv[1] if len(sys.argv) > 1 else "84332968853"
    out = check_account_exist_playwright(p, headless=True)
    print(json.dumps(out, ensure_ascii=False, indent=2))
