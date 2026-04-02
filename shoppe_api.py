import requests
from typing import Dict, Any, Optional

class ShopeeClient:
    def __init__(self, cookies: Optional[str] = None, proxies: Optional[Dict[str, str]] = None):
        """
        Khởi tạo Shopee Client
        :param cookies: (Tùy chọn) Chuỗi cookie nếu Shopee yêu cầu xác thực hoặc chặn bot
        :param proxies: (Tùy chọn) Cấu hình proxy dưới dạng dict, VD: {"http": "...", "https": "..."}
        """
        self.base_url = "https://shopee.vn"
        
        # Headers cơ bản dựa trên request mẫu của bạn
        self.headers = {
            "User-Agent": "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:130.0) Gecko/20100101 Firefox/130.0",
            "Accept": "application/json",
            "Accept-Language": "en-US,en;q=0.5",
            "Content-Type": "application/json",
            "Referer": "https://shopee.vn/buyer/reset?scenario=7",
            "X-Shopee-Language": "vi",
            "X-Requested-With": "XMLHttpRequest",
            "X-API-SOURCE": "pc",
            "x-sz-sdk-version": "1.12.33",
        }

        if cookies:
            self.headers["Cookie"] = cookies
        
        self.proxies = proxies

    def check_account_exist(self, phone: str, csrftoken: Optional[str] = None) -> Dict[str, Any]:
        """
        Kiểm tra xem số điện thoại đã được đăng ký trên Shopee chưa.
        
        :param phone: Số điện thoại cần kiểm tra (VD: '84332968853')
        :param csrftoken: Token CSRF (thường bắt buộc trong các request POST của Shopee)
        :return: JSON cấu trúc trả về từ API
        """
        url = f"{self.base_url}/api/v4/account/basic/check_account_exist"
        payload = {
            "phone": phone,
            "scenario": 2
        }

        headers = self.headers.copy()
        
        # Một số endpoint của Shopee yêu cầu CSRF token khớp zvới trong cookie
        if csrftoken:
            headers["X-CSRFToken"] = csrftoken
            
        try:
            response = requests.post(url, json=payload, headers=headers, proxies=self.proxies)
            
            # Trả về nguyên kết quả JSON để tiện phân tích
            return response.json()
        except requests.exceptions.RequestException as e:
            return {"error": str(e), "message": "Lỗi khi gọi API Shopee"}

if __name__ == "__main__":
    client = ShopeeClient()
    print("--- Đang thử kiểm tra số điện thoại ---")
    result = client.check_account_exist("84332968853")
    print("Kết quả:", result)
