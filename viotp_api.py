import requests
from typing import Optional, Dict, Any

class ViOTPClient:
    def __init__(self, token: str):
        """
        Khởi tạo ViOTP Client
        :param token: API token của bạn
        """
        self.token = token
        self.base_url = "https://api.viotp.com"

    def request_number(self, service_id: int, network: Optional[str] = None, 
                       prefix: Optional[str] = None, except_prefix: Optional[str] = None, 
                       number: Optional[str] = None, country: Optional[str] = None) -> Dict[str, Any]:
        """
        Yêu cầu thuê số điện thoại mới
        
        :param service_id: Id của dịch vụ (ví dụ: Shopee = 2 theo danh sách)
        :param network: Tùy chọn nhà mạng (VD: 'MOBIFONE|VINAPHONE')
        :param prefix: Tùy chọn đầu số muốn lấy (VD: '90|91')
        :param except_prefix: Tùy chọn đầu số không muốn lấy (VD: '94|96')
        :param number: Số điện thoại muốn thuê lại (để nhận OTP lần nữa)
        :param country: Mã quốc gia ('vn' hoặc 'la', mặc định vn)
        :return: Phản hồi JSON từ API
        """
        url = f"{self.base_url}/request/getv2"
        params = {
            "token": self.token,
            "serviceId": service_id
        }
        if network: params["network"] = network
        if prefix: params["prefix"] = prefix
        if except_prefix: params["exceptPrefix"] = except_prefix
        if number: params["number"] = number
        if country: params["country"] = country

        response = requests.get(url, params=params)
        return response.json()

    def get_code(self, request_id: str) -> Dict[str, Any]:
        """
        Lấy code (OTP) của một số đã thuê thông qua request_id
        
        :param request_id: Mã lấy từ kết quả thuê số
        :return: Phản hồi JSON từ API chứa mã OTP
        """
        url = f"{self.base_url}/session/getv2"
        params = {
            "token": self.token,
            "requestId": request_id
        }
        response = requests.get(url, params=params)
        return response.json()
