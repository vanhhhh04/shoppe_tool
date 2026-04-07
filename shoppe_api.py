"""
Shopee web API client (không phải public API chính thức).

Shopee dùng cookie session, CSRF khớp cookie↔header, và nhiều header anti-bot
sinh trong JS (af-ac-enc-*, x-sap-*, …). Chỉ thêm header cố định thường không
đủ; response vẫn 200 nhưng có thể error/need_verify. Proxy/datacenter IP cũng
dễ bị hạn chế. Cách bền: Playwright/Selenium trong context trình duyệt thật.
"""

from __future__ import annotations

import requests
from typing import TYPE_CHECKING, Any, Dict, Optional

if TYPE_CHECKING:
    from kiotproxy_api import Proxy as KiotProxy


def _cookie_value(cookie_header: str, name: str) -> Optional[str]:
    """Lấy giá trị một cookie từ chuỗi Cookie header ('a=1; b=2')."""
    if not cookie_header:
        return None
    prefix = name + "="
    for part in cookie_header.split(";"):
        part = part.strip()
        if part.startswith(prefix):
            return part[len(prefix) :].strip() or None
    return None


class ShopeeClient:
    """
    Client gọi endpoint kiểu trình duyệt.

    Nhóm header cơ bản: User-Agent, Accept, Content-Type, Origin, Referer.
    CSRF: cookie ``csrftoken`` và header ``X-CSRFToken`` phải trùng giá trị.
    Cookie/anti-bot đầy đủ và token JS thường cần lấy từ browser (hoặc copy từ DevTools).
    """

    def __init__(
        self,
        cookies: Optional[str] = None,
        proxies: Optional[Dict[str, str]] = None,
        kiot_proxy: Optional["KiotProxy"] = None,
        session: Optional[requests.Session] = None,
        *,
        referer: str = "https://shopee.vn/buyer/reset?scenario=7",
        sync_csrf_from_cookie: bool = True,
    ):
        """
        :param cookies: Chuỗi Cookie đầy đủ từ trình duyệt (khuyến nghị khi chưa dùng automation).
        :param proxies: Proxy tĩnh cho requests.
        :param kiot_proxy: KiotProxy tự xoay; Shopee có thể chặn IP proxy.
        :param session: ``requests.Session`` tùy chỉnh; mặc định tạo session mới (giữ jar cookie sau warmup).
        :param referer: Referer khớp ngữ cảnh trang (reset password, đăng nhập, …).
        :param sync_csrf_from_cookie: Nếu ``cookies`` có ``csrftoken=``, gán luôn ``X-CSRFToken``.
        """
        self.base_url = "https://shopee.vn"
        self.proxies = proxies
        self.kiot_proxy = kiot_proxy
        self._session = session if session is not None else requests.Session()

        self.headers: Dict[str, str] = {
            "User-Agent": (
                "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:130.0) Gecko/20100101 Firefox/130.0"
            ),
            "Accept": "application/json",
            "Accept-Language": "en-US,en;q=0.5",
            "Content-Type": "application/json",
            "Origin": self.base_url,
            "Referer": referer,
            "X-Shopee-Language": "vi",
            "X-Requested-With": "XMLHttpRequest",
            "X-API-SOURCE": "pc",
            "x-sz-sdk-version": "1.12.33",
        }

        if cookies:
            self.set_cookie_header(cookies, sync_csrf=sync_csrf_from_cookie)

    def set_cookie_header(self, cookie_header: str, *, sync_csrf: bool = True) -> None:
        """Gán ``Cookie``; tùy chọn đồng bộ ``X-CSRFToken`` từ ``csrftoken`` trong chuỗi."""
        self.headers["Cookie"] = cookie_header
        if sync_csrf:
            csrf = _cookie_value(cookie_header, "csrftoken")
            if csrf:
                self.headers["X-CSRFToken"] = csrf
            else:
                self.headers.pop("X-CSRFToken", None)

    def set_csrf_token(self, token: str) -> None:
        """Gán header X-CSRFToken (phải khớp cookie csrftoken nếu server kiểm tra)."""
        self.headers["X-CSRFToken"] = token

    def _effective_proxies(self) -> Optional[Dict[str, str]]:
        if self.kiot_proxy is not None:
            self.kiot_proxy.ensure_valid()
            return self.kiot_proxy.as_requests_proxies()
        return self.proxies

    def warmup(self, path: str = "/", timeout: float = 30.0) -> requests.Response:
        """
        GET trang Shopee để nhận cookie vào session jar (ví dụ csrftoken).

        Không thay thế token anti-bot sinh bằng JS; thường vẫn cần cookie đầy đủ
        từ browser hoặc automation.
        """
        url = f"{self.base_url}{path}" if path.startswith("/") else f"{self.base_url}/{path}"
        send_headers = {k: v for k, v in self.headers.items() if k.lower() != "content-type"}
        r = self._session.get(
            url,
            headers=send_headers,
            proxies=self._effective_proxies(),
            timeout=timeout,
        )
        jar_csrf = self._session.cookies.get("csrftoken")
        if jar_csrf:
            self.headers["X-CSRFToken"] = jar_csrf
            # Giúp request sau gửi đúng csrftoken nếu chưa paste full Cookie
            self._refresh_cookie_header_from_session()
        return r

    def _refresh_cookie_header_from_session(self) -> None:
        if not self._session.cookies:
            return
        self.headers["Cookie"] = "; ".join(f"{c.name}={c.value}" for c in self._session.cookies)

    def check_account_exist(self, phone: str, csrftoken: Optional[str] = None) -> Dict[str, Any]:
        """
        Kiểm tra số đã đăng ký (scenario=2).

        ``scenario`` trên API kiểu browser: 1 đăng nhập, 2 check tồn tại,
        7 reset mật khẩu (tham khảo DevTools).

        :param phone: Số điện thoại (VD ``84332968853``).
        :param csrftoken: Nếu truyền, ghi đè ``X-CSRFToken`` cho request này (vẫn nên khớp cookie).
        """
        url = f"{self.base_url}/api/v4/account/basic/check_account_exist"
        payload = {"phone": phone, "scenario": 2}

        headers = self.headers.copy()
        if csrftoken is not None:
            headers["X-CSRFToken"] = csrftoken

        try:
            response = self._session.post(
                url,
                json=payload,
                headers=headers,
                proxies=self._effective_proxies(),
            )
            return response.json()
        except requests.exceptions.RequestException as e:
            return {"error": str(e), "message": "Lỗi khi gọi API Shopee"}


if __name__ == "__main__":
    client = ShopeeClient()
    print("--- Đang thử kiểm tra số điện thoại ---")
    result = client.check_account_exist("84332968853")
    print("Kết quả:", result)
