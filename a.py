from __future__ import annotations

import json
import os
import random
import re
import time
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union, cast

from playwright.sync_api import (
    Browser,
    BrowserContext,
    Frame,
    Locator,
    Page,
    Playwright,
    Response,
    sync_playwright,
)
from playwright_stealth import Stealth

from kiotproxy_api import Proxy, get_new_proxy_response

RESET_URL = "https://shopee.vn/buyer/reset?scenario=7"
CHECK_PATH = "/api/v4/account/basic/check_account_exist"

# HTTP 200 nhưng JSON có error=90309999: thường là từ chối risk/anti-abuse (thiếu token/header
# mà chỉ JS của Shopee hoặc đúng luồng UI mới gửi). Không phải exception Python.
SHOPEE_ERROR_RISK_GENERIC = 90309999

# Pool UA/viewport — mỗi ``BrowserContext`` = một fingerprint nhẹ (cùng Kiot: 1 IP / session).
# Chỉ Chrome/Edge: Playwright dùng Chromium; ``playwright_stealth`` parse ``Chrome/<ver>`` cho Sec-CH-UA —
# Firefox UA gây crash (regex trả None trong thư viện).
_DESKTOP_USER_AGENTS: Tuple[str, ...] = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0",
)

_UA_HAS_CHROME_VERSION = re.compile(r"Chrome/\d+", re.IGNORECASE)

_VIEWPORTS: Tuple[Dict[str, int], ...] = (
    {"width": 1920, "height": 1080},
    {"width": 1366, "height": 768},
    {"width": 1536, "height": 864},
    {"width": 1280, "height": 800},
    {"width": 1440, "height": 900},
    {"width": 1600, "height": 900},
)


def random_user_agent() -> str:
    return random.choice(_DESKTOP_USER_AGENTS)


def random_viewport() -> Dict[str, int]:
    return dict(random.choice(_VIEWPORTS))


def _prepare_shopee_page(page: Page, *, user_agent: str) -> None:
    """Stealth khớp ``user_agent`` của context (gọi ngay sau ``new_page()``, trước ``goto``)."""
    stealth_kw: Dict[str, Any] = {
        "navigator_languages_override": ("vi-VN", "vi"),
        "navigator_user_agent_override": user_agent,
        "navigator_platform_override": "Win32",
    }
    if not _UA_HAS_CHROME_VERSION.search(user_agent):
        # Tránh __init__ gọi _get_greased_chrome_sec_ua_ch khi không có "Chrome/<ver>".
        stealth_kw["sec_ch_ua"] = False
        stealth_kw["sec_ch_ua_override"] = ""
    Stealth(**stealth_kw).apply_stealth_sync(page)


def _random_delay_before_account_check() -> None:
    """Nghỉ ngẫu nhiên sau warmup trang, trước khi gọi check (UI / fetch)."""
    time.sleep(random.uniform(1.5, 3.5))


def kiot_key_from_environ() -> str:
    """
    Proxy Key cho ``?key=`` (sidebar **Key**), **không** phải API Token (Cài đặt → API Token).

    Nếu đặt ``KIOTPROXY_PROXY_KEY`` trong .env thì **chỉ** dùng biến này (không fallback
    ``kiotproxy_token``). Bỏ hẳn dòng ``KIOTPROXY_PROXY_KEY`` nếu muốn dùng tên biến cũ.
    """
    if "KIOTPROXY_PROXY_KEY" in os.environ:
        return os.environ["KIOTPROXY_PROXY_KEY"].strip()
    return (
        os.environ.get("kiotproxy_token", "").strip()
        or os.environ.get("KIOTPROXY_KEY", "").strip()
    )


def kiot_proxy_from_env(region: Optional[str] = None) -> Optional[Proxy]:
    """
    Tạo ``Proxy`` từ env: giá trị **chính là Proxy Key** mà API Kiot truyền dưới dạng ``?key=...``.

    Biến: ``KIOTPROXY_PROXY_KEY`` (khuyến nghị), hoặc ``kiotproxy_token`` / ``KIOTPROXY_KEY`` —
    **Proxy Key** từ menu Key, không dùng API Token.
    """
    key = kiot_key_from_environ()
    if not key:
        return None
    reg = region if region is not None else os.environ.get("KIOTPROXY_REGION", "random")
    return Proxy(key=key, region=reg)


@dataclass
class ShopeeUiTiming:
    """
    Gom **toàn bộ** chờ / timeout (ms hoặc giây) — chỉnh tại đây hoặc ``ShopeeUiTiming.fast()``.

    ``goto_wait_until``: ``domcontentloaded`` nhanh hơn ``load``; ``networkidle_ms`` ≤ 0 = bỏ bước đó.
    """

    # "load" chậm hơn; dùng "commit" trong .fast() để về sớm nhất (dễ trang chưa kịp render).
    goto_wait_until: str = "domcontentloaded"
    goto_ms: int = 18_000
    # ≤0: không chờ networkidle (tiết kiệm vài giây khi Shopee giữ kết nối mở).
    networkidle_ms: int = 400
    hydrate_sleep_min_s: float = 0.04
    hydrate_sleep_max_s: float = 0.11

    popup_btn_click_ms: int = 120
    popup_sleep_after_s: float = 0.015

    phone_resolve_total_ms: int = 5_500
    phone_probe_visible_ms: int = 100
    phone_poll_interval_s: float = 0.02

    modal_rounds: int = 1
    modal_escape_sleep_s: float = 0.005
    modal_btn_click_ms: int = 220
    modal_after_btn_sleep_s: float = 0.018
    modal_close_click_ms: int = 140
    modal_after_close_sleep_s: float = 0.018
    modal_round_sleep_s: float = 0.005
    modal_pointer_none_sleep_s: float = 0.012

    scroll_into_view_ms: int = 1_400
    focus_ms: int = 1_800
    focus_fallback_force_click_ms: int = 1_200
    key_delay_ms: int = 0
    key_delay_fallback_ms: int = 1

    expect_check_account_api_ms: int = 7_000
    next_btn_click_ms: int = 1_800
    next_btn_force_click_ms: int = 1_200

    @classmethod
    def fast(cls) -> ShopeeUiTiming:
        """Tối đa: commit + bỏ networkidle + timeout rất thấp."""
        return cls(
            goto_wait_until="commit",
            goto_ms=14_000,
            networkidle_ms=0,
            hydrate_sleep_min_s=0.01,
            hydrate_sleep_max_s=0.03,
            phone_resolve_total_ms=3_500,
            phone_probe_visible_ms=60,
            phone_poll_interval_s=0.012,
            modal_rounds=1,
            modal_escape_sleep_s=0.002,
            modal_btn_click_ms=120,
            modal_after_btn_sleep_s=0.008,
            modal_close_click_ms=90,
            modal_after_close_sleep_s=0.008,
            modal_round_sleep_s=0.002,
            modal_pointer_none_sleep_s=0.006,
            scroll_into_view_ms=900,
            focus_ms=1_100,
            focus_fallback_force_click_ms=750,
            key_delay_ms=0,
            key_delay_fallback_ms=1,
            expect_check_account_api_ms=5_000,
            next_btn_click_ms=1_100,
            next_btn_force_click_ms=750,
            popup_btn_click_ms=90,
            popup_sleep_after_s=0.01,
        )


def _cookie_value(cookies: List[Dict[str, Any]], name: str) -> Optional[str]:
    for c in cookies:
        if c.get("name") == name:
            return c.get("value")
    return None


class ShopeeBrowserClient:
    """
    Playwright sync API phải chạy trên **một thread** (bên trong dùng greenlet).

    Không dùng ThreadPoolExecutor cho cùng một Playwright/browser.
    Nếu cần nhiều số song song: mở nhiều terminal / subprocess, mỗi process một script.

    Ưu tiên ``check_account_exist_via_ui`` (điền form như người dùng) — tránh mã 90309999
    hay gặp khi tự ``fetch`` trong ``page.evaluate``.

    Trên cloud, **headless vẫn là “có trình duyệt”** (Chromium không cửa sổ), không bị cấm
    theo nghĩa “không được mở browser”. Shopee có thể **trả trang khác** khi headless
    (wall/captcha) — dùng ``--debug-dir`` xem screenshot; Docker Linux thường cần
    ``SHOPEE_PW_NO_SANDBOX=1``.
    """

    def __init__(
        self,
        headless: bool = False,
        use_system_chrome: bool = False,
        *,
        kiot_proxy: Optional[Proxy] = None,
        rotate_kiot_each_session: bool = True,
        kiot_rotate_after_seconds: float = 0.0,
        debug_dir: Optional[Union[str, Path]] = None,
        timing: Optional[ShopeeUiTiming] = None,
    ):
        self.headless = headless
        self.use_system_chrome = use_system_chrome
        self.kiot_proxy = kiot_proxy
        self.rotate_kiot_each_session = rotate_kiot_each_session
        self.kiot_rotate_after_seconds = max(0.0, float(kiot_rotate_after_seconds))
        self.debug_dir = Path(debug_dir) if debug_dir else None
        self.timing = timing if timing is not None else ShopeeUiTiming()
        self._pw_cm: Optional[AbstractContextManager[Playwright]] = None
        self._pw: Optional[Playwright] = None
        self.browser: Optional[Browser] = None
        self._last_kiot_rotate_at: Optional[float] = None

    def __enter__(self) -> ShopeeBrowserClient:
        self.start()
        return self

    def __exit__(self, *args: object) -> None:
        self.stop()

    def start(self) -> None:
        cm = sync_playwright()
        self._pw_cm = cm
        self._pw = cm.__enter__()
        extra_args = ["--disable-blink-features=AutomationControlled"]
        # Trong Docker/Linux cloud thường cần: SHOPEE_PW_NO_SANDBOX=1
        if os.environ.get("SHOPEE_PW_NO_SANDBOX", "").strip() in ("1", "true", "yes"):
            extra_args.extend(["--no-sandbox", "--disable-setuid-sandbox"])
        launch_kwargs: Dict[str, Any] = {
            "headless": self.headless,
            "args": extra_args,
        }
        if self.use_system_chrome:
            launch_kwargs["channel"] = "chrome"
        assert self._pw is not None
        self.browser = self._pw.chromium.launch(**launch_kwargs)

    def stop(self) -> None:
        try:
            if self.browser:
                self.browser.close()
                self.browser = None
            if self.kiot_proxy is not None:
                try:
                    self.kiot_proxy.release()
                except Exception:
                    pass
        finally:
            if self._pw_cm is not None:
                self._pw_cm.__exit__(None, None, None)
                self._pw_cm = None
                self._pw = None

    def _prepare_kiot_proxy_for_context(self) -> None:
        """
        Đồng nhất logic xoay proxy:
        - Nếu có ``kiot_rotate_after_seconds``: xoay theo chu kỳ này.
        - Nếu không cấu hình chu kỳ: dựa vào thời gian Kiot trả về (ttc/nextRequestAt),
          chỉ xoay khi đã tới lượt để tránh chờ ``TIME_TO_CHANGE_INVALID``.
        """
        if self.kiot_proxy is None:
            return
        if not self.rotate_kiot_each_session:
            self.kiot_proxy.ensure_valid()
            return

        now = time.time()
        if self.kiot_rotate_after_seconds > 0:
            if (
                self._last_kiot_rotate_at is None
                or (now - self._last_kiot_rotate_at) >= self.kiot_rotate_after_seconds
            ):
                self.kiot_proxy.prepare_new_session()
                self._last_kiot_rotate_at = now
            else:
                self.kiot_proxy.ensure_valid()
            return

        self.kiot_proxy.ensure_valid()
        timing = self.kiot_proxy.rotation_timing() or {}
        can_new_in = timing.get("seconds_until_can_request_new")
        ttc = timing.get("ttc_seconds_until_next_change")
        if (
            isinstance(can_new_in, (int, float)) and can_new_in <= 0.0
        ) or (
            isinstance(ttc, (int, float)) and ttc <= 0.0
        ):
            self.kiot_proxy.prepare_new_session()
            self._last_kiot_rotate_at = now

    def _new_browser_context(self) -> Tuple[BrowserContext, str]:
        """
        Một context = một phiên: UA + viewport ngẫu nhiên.
        Nếu có KiotProxy: dùng logic xoay thông minh để tránh bị chờ do gọi đổi quá sớm.
        """
        assert self.browser is not None
        self._prepare_kiot_proxy_for_context()

        ua = random_user_agent()
        viewport = random_viewport()
        headers = {
            "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
        }
        ctx_kwargs: Dict[str, Any] = {
            "viewport": viewport,
            "locale": "vi-VN",
            "timezone_id": "Asia/Ho_Chi_Minh",
            "user_agent": ua,
            "extra_http_headers": headers,
        }
        if self.kiot_proxy is not None:
            ctx_kwargs["proxy"] = self.kiot_proxy.as_playwright_proxy()

        return self.browser.new_context(**ctx_kwargs), ua

    def _warmup_page(self, page: Any) -> None:
        t = self.timing
        page.goto(RESET_URL, wait_until=t.goto_wait_until, timeout=t.goto_ms)
        if t.networkidle_ms > 0:
            try:
                page.wait_for_load_state("networkidle", timeout=t.networkidle_ms)
            except Exception:
                pass
        page.mouse.move(200, 300)
        page.mouse.wheel(0, 500)
        time.sleep(random.uniform(t.hydrate_sleep_min_s, t.hydrate_sleep_max_s))

    def _save_debug_shot(self, page: Page, name: str) -> None:
        if not self.debug_dir:
            return
        self.debug_dir.mkdir(parents=True, exist_ok=True)
        path = self.debug_dir / name
        try:
            page.screenshot(path=str(path), full_page=True)
        except Exception:
            pass

    @staticmethod
    def _locator_first_visible(locator: Locator, ms: int) -> bool:
        try:
            locator.first.wait_for(state="visible", timeout=ms)
            return True
        except Exception:
            return False

    def _resolve_phone_input(self, page: Page) -> Locator:
        """Ô SĐT có thể không phải type=tel hoặc nằm trong iframe (đặc biệt headless)."""
        t = self.timing
        deadline = time.monotonic() + t.phone_resolve_total_ms / 1000.0
        selectors = [
            'input[type="tel"]',
            'input[inputmode="numeric"]',
            'input[autocomplete="tel"]',
            'input[autocomplete="tel-national"]',
        ]
        ph_re = re.compile(
            r"điện\s*thoại|số\s*điện\s*thoại|phone|mobile|cellphone|sdt",
            re.I,
        )
        while time.monotonic() < deadline:
            frames: List[Frame] = []
            seen = set()
            for fr in [page.main_frame, *page.frames]:
                if fr not in seen:
                    seen.add(fr)
                    frames.append(fr)
            for fr in frames:
                for sel in selectors:
                    loc = fr.locator(sel)
                    if self._locator_first_visible(loc, t.phone_probe_visible_ms):
                        return loc.first
                try:
                    loc = fr.get_by_placeholder(ph_re)
                    if self._locator_first_visible(loc, t.phone_probe_visible_ms):
                        return loc.first
                except Exception:
                    pass
            time.sleep(t.phone_poll_interval_s)
        raise TimeoutError(
            "Không thấy ô nhập số điện thoại. "
            "Headless có thể gặp captcha/wall — chạy --headed hoặc xem ảnh --debug-dir.",
        )

    def _dismiss_common_popups(self, page: Page) -> None:
        t = self.timing
        for label in ("Đóng", "Hoàn tất", "Đồng ý", "Close"):
            try:
                page.get_by_role(
                    "button",
                    name=re.compile(f"^{re.escape(label)}$", re.I),
                ).click(timeout=t.popup_btn_click_ms)
                time.sleep(t.popup_sleep_after_s)
            except Exception:
                pass

    def _dismiss_modal_overlay(self, page: Page) -> None:
        """
        Shopee hay bọc full-screen ``#modal`` (worldmap / lớp trong suốt) chặn pointer —
        ``click`` vào ô phone báo intercepts pointer events.
        """
        t = self.timing
        modal = page.locator("#modal")
        for _ in range(t.modal_rounds):
            for _ in range(2):
                page.keyboard.press("Escape")
                time.sleep(t.modal_escape_sleep_s)
            try:
                if modal.count() == 0 or not modal.first.is_visible():
                    return
            except Exception:
                return
            try:
                btn = modal.get_by_role(
                    "button",
                    name=re.compile(r"đóng|close|bỏ qua|skip|để sau|hoàn tất", re.I),
                )
                if btn.count() > 0:
                    btn.first.click(timeout=t.modal_btn_click_ms)
                    time.sleep(t.modal_after_btn_sleep_s)
            except Exception:
                pass
            try:
                modal.locator('[class*="close"]').first.click(timeout=t.modal_close_click_ms)
                time.sleep(t.modal_after_close_sleep_s)
            except Exception:
                pass
            time.sleep(t.modal_round_sleep_s)

        # Lớp intro/worldmap trong #modal vẫn chặn click — cho phép tương tác xuyên qua.
        try:
            if modal.count() > 0 and modal.first.is_visible():
                page.evaluate(
                    """() => {
                        const m = document.getElementById('modal');
                        if (m) m.style.pointerEvents = 'none';
                    }"""
                )
                time.sleep(t.modal_pointer_none_sleep_s)
        except Exception:
            pass

    def _focus_and_fill_phone(self, tel: Locator, phone: str) -> None:
        """Tránh ``click`` khi bị lớp modal che; ưu tiên ``focus`` + gõ."""
        t = self.timing
        try:
            tel.focus(timeout=t.focus_ms)
        except Exception:
            tel.click(force=True, timeout=t.focus_fallback_force_click_ms)
        tel.fill("")
        try:
            tel.press_sequentially(phone, delay=t.key_delay_ms)
        except AttributeError:
            try:
                tel.type(phone, delay=t.key_delay_fallback_ms)
            except Exception:
                tel.fill(phone)

    @staticmethod
    def _evaluate_check(page: Any, phone: str, csrf: str) -> dict:
        return page.evaluate(
            """async ({ path, phone, csrf, origin, referer }) => {
                const headers = {
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "X-Requested-With": "XMLHttpRequest",
                    "Origin": origin,
                    "Referer": referer,
                    "X-API-SOURCE": "pc",
                    "X-Shopee-Language": "vi",
                    "x-sz-sdk-version": "1.12.33",
                };
                if (csrf) headers["X-CSRFToken"] = csrf;
                const res = await fetch(path, {
                    method: "POST",
                    credentials: "include",
                    headers,
                    body: JSON.stringify({ phone, scenario: 2 }),
                });
                const text = await res.text();
                let data;
                try {
                    data = JSON.parse(text);
                } catch (e) {
                    data = { _parseError: String(e), _raw: text.slice(0, 500), _httpStatus: res.status };
                }
                return {
                    _debug: { hadCsrfHeader: !!csrf, httpStatus: res.status },
                    ...data,
                };
            }""",
            {
                "path": CHECK_PATH,
                "phone": phone,
                "csrf": csrf or "",
                "origin": "https://shopee.vn",
                "referer": RESET_URL,
            },
        )

    def check_account_exist_via_ui(self, page: Page, phone: str) -> Dict[str, Any]:
        """
        Điền ô số điện thoại và bấm nút bước tiếp — bắt POST ``check_account_exist``
        do **frontend Shopee** gửi (header/token động đầy đủ hơn so với fetch tay trong evaluate).
        """
        self._dismiss_common_popups(page)
        tel = self._resolve_phone_input(page)
        tel.scroll_into_view_if_needed(timeout=self.timing.scroll_into_view_ms)
        self._dismiss_modal_overlay(page)
        self._focus_and_fill_phone(tel, phone)

        def matches_check(resp: Response) -> bool:
            return resp.request.method == "POST" and "check_account_exist" in resp.url

        try:
            with page.expect_response(
                matches_check, timeout=self.timing.expect_check_account_api_ms
            ) as resp_wait:
                next_btn = page.get_by_role("button").filter(
                    has_text=re.compile(r"tiếp|next|continue|gửi|submit", re.I)
                )
                if next_btn.count() == 0:
                    next_btn = page.locator("button").filter(
                        has_text=re.compile(r"tiếp|next|continue|gửi|submit", re.I)
                    )
                self._dismiss_modal_overlay(page)
                nb = next_btn.first
                try:
                    nb.click(timeout=self.timing.next_btn_click_ms)
                except Exception:
                    nb.click(force=True, timeout=self.timing.next_btn_force_click_ms)
            api_resp = resp_wait.value
        except Exception:
            self._save_debug_shot(page, f"ui-fail-{phone}-{int(time.time())}.png")
            raise

        try:
            return api_resp.json()
        except Exception:
            raw = api_resp.text()
            return {
                "_http_status": api_resp.status,
                "_body_preview": (raw or "")[:500],
                "error": "invalid_json_from_shopee",
            }

    def check_account_exist(self, phone: str, *, use_ui: bool = True) -> dict:
        """Một số — cần đã ``start()``. Mặc định dùng luồng UI (giảm 90309999)."""
        assert self.browser is not None
        ctx, ua = self._new_browser_context()
        page = ctx.new_page()
        _prepare_shopee_page(page, user_agent=ua)
        try:
            self._warmup_page(page)
            _random_delay_before_account_check()
            if use_ui:
                try:
                    body = self.check_account_exist_via_ui(page, phone)
                    body = {**body, "_via": "ui"}
                    return body
                except Exception as e:
                    csrf = _cookie_value(cast(List[Dict[str, Any]], ctx.cookies()), "csrftoken") or ""
                    fetched = self._evaluate_check(page, phone, csrf)
                    return {
                        **fetched,
                        "_via": "fetch_fallback",
                        "_ui_error": str(e),
                    }
            csrf = _cookie_value(cast(List[Dict[str, Any]], ctx.cookies()), "csrftoken") or ""
            out = self._evaluate_check(page, phone, csrf)
            return {**out, "_via": "fetch"}
        finally:
            ctx.close()

    def check_multiple_accounts(
        self,
        phones: List[str],
        *,
        use_ui: bool = True,
    ) -> List[Tuple[str, Dict[str, Any]]]:
        """
        Tuần tự: một browser, mỗi số một context mới — **cùng thread**, an toàn cho sync_api.
        """
        assert self.browser is not None
        out: List[Tuple[str, Dict[str, Any]]] = []
        for phone in phones:
            ctx, ua = self._new_browser_context()
            page = ctx.new_page()
            _prepare_shopee_page(page, user_agent=ua)
            try:
                self._warmup_page(page)
                _random_delay_before_account_check()
                if use_ui:
                    try:
                        result = self.check_account_exist_via_ui(page, phone)
                        result = {**result, "_via": "ui"}
                    except Exception as e:
                        csrf = (
                            _cookie_value(cast(List[Dict[str, Any]], ctx.cookies()), "csrftoken")
                            or ""
                        )
                        fetched = self._evaluate_check(page, phone, csrf)
                        result = {**fetched, "_via": "fetch_fallback", "_ui_error": str(e)}
                else:
                    csrf = _cookie_value(cast(List[Dict[str, Any]], ctx.cookies()), "csrftoken") or ""
                    result = {**self._evaluate_check(page, phone, csrf), "_via": "fetch"}
                out.append((phone, result))
            finally:
                ctx.close()
        return out


if __name__ == "__main__":
    import argparse

    from dotenv import load_dotenv

    load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env", override=True)

    parser = argparse.ArgumentParser(
        description="Batch check_account_exist — mặc định headless (không bật cửa sổ Chrome).",
    )
    parser.add_argument(
        "--headed",
        action="store_true",
        help="Bật cửa sổ trình duyệt (debug, captcha thủ công).",
    )
    parser.add_argument(
        "--chrome",
        action="store_true",
        help="Dùng Google Chrome cài trên máy (channel=chrome); mặc định dùng Chromium của Playwright.",
    )
    parser.add_argument(
        "--debug-dir",
        default=None,
        metavar="DIR",
        help="Thư mục lưu screenshot khi bước UI/API fail (headless debug).",
    )
    parser.add_argument(
        "--fast",
        action="store_true",
        help="Rút ngắn sleep/timeout (xem ShopeeUiTiming.fast); nhanh hơn, dễ fail hơn nếu mạng chậm.",
    )
    parser.add_argument(
        "--kiot-key",
        default=kiot_key_from_environ(),
        metavar="KEY",
        help=(
            "Proxy Key (menu Key), không phải API Token. Env: KIOTPROXY_PROXY_KEY, kiotproxy_token…"
        ),
    )
    parser.add_argument(
        "--kiot-region",
        default=os.environ.get("KIOTPROXY_REGION", "random"),
        help="bac | trung | nam | random (mặc định random hoặc KIOTPROXY_REGION).",
    )
    parser.add_argument(
        "--reuse-proxy-ip",
        action="store_true",
        help="Không gọi prepare_new_session giữa các số (cùng IP cho cả batch — không khuyến nghị).",
    )
    parser.add_argument(
        "--kiot-rotate-after",
        type=float,
        default=float(os.environ.get("KIOT_ROTATE_AFTER_SECONDS", "0") or 0),
        help=(
            "Xoay Kiot theo số giây cố định (0 = dùng ttc/nextRequestAt từ API để "
            "tránh gọi đổi quá sớm). Có thể đặt qua KIOT_ROTATE_AFTER_SECONDS."
        ),
    )
    parser.add_argument(
        "--kiot-verify",
        action="store_true",
        help="Chỉ gọi Kiot GET /proxies/new rồi thoát (kiểm tra Proxy Key, không mở browser).",
    )
    args = parser.parse_args()

    if args.kiot_verify:
        kv = (args.kiot_key or "").strip() or kiot_key_from_environ()
        if not kv:
            parser.error("Cần Proxy Key: --kiot-key hoặc kiotproxy_token / KIOTPROXY_KEY trong .env")
        out = get_new_proxy_response(kv, args.kiot_region)
        print(json.dumps(out, ensure_ascii=False, indent=2))
        raise SystemExit(0 if out.get("success") else 1)

    phones = [
        "84565132248",
        "84568527580"
    ]

    # headless mặc định: không hiện cửa sổ. Nếu Shopee siết headless: thử --headed hoặc giảm tốc độ.
    start_time = time.time()
    kiot: Optional[Proxy] = None
    if args.kiot_key:
        kiot = Proxy(key=args.kiot_key.strip(), region=args.kiot_region)
    else:
        kiot = kiot_proxy_from_env(region=args.kiot_region)

    timing = ShopeeUiTiming.fast() if args.fast else ShopeeUiTiming()
    with ShopeeBrowserClient(
        headless=not args.headed,
        use_system_chrome=args.chrome,
        kiot_proxy=kiot,
        rotate_kiot_each_session=not args.reuse_proxy_ip,
        kiot_rotate_after_seconds=args.kiot_rotate_after,
        debug_dir=args.debug_dir,
        timing=timing,
    ) as client:
        results = client.check_multiple_accounts(phones, use_ui=True)
    print(json.dumps(results, ensure_ascii=False, indent=2))
    print(
        f"\nNếu vẫn thấy error={SHOPEE_ERROR_RISK_GENERIC}: đó là mã từ Shopee (HTTP vẫn 200), "
        "thường do risk/anti-abuse — thử IP/mạng khác, giảm tần suất, hoặc hoàn tất verify trên trình duyệt.",
    )
    end_time = time.time()
    print(f"Thời gian chạy: {end_time - start_time:.2f} giây")
