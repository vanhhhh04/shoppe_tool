import os
import time
from dotenv import load_dotenv
from viotp_api import ViOTPClient

load_dotenv()

viotp_token = os.getenv("viotp_token")
print(f"Token: {viotp_token}")

if viotp_token:
    client = ViOTPClient(token=viotp_token)
    
    # 1. Gọi thử API thuê số. Giá trị service_id thay bằng dịch vụ muốn thuê (VD: 2 có thể là Shopee, 1 là Momo...)
    print("\n--- Đang thuê số ---")
    service_id = 1 
    request_result = client.request_number(service_id=service_id)
    print("Kết quả thuê số:", request_result)
    
    if request_result.get("status_code") == 200:
        request_data = request_result.get("data", {})
        phone_number = request_data.get("phone_number")
        request_id = request_data.get("request_id")
        
        print(f"\n=> Thuê thành công số: {phone_number}")
        print(f"=> Request ID: {request_id}")
        
        # 2. Lấy code của số vừa thuê (thông thường phải chờ 1 lúc thì tin nhắn mới tới)
        print("\n--- Đang lấy code (chờ 10 giây để SMS có thể gửi tới) ---")
        time.sleep(10) # Chờ vài giây để có thời gian tin nhắn tới
        code_result = client.get_code(request_id=request_id)
        print("Kết quả lấy mã OTP:", code_result)
        
        if code_result.get("status_code") == 200:
            otp_data = code_result.get("data", {})
            print("\n===============================")
            print("MÃ OTP CỦA BẠN LÀ:", otp_data.get("Code"))
            print("Nội dung tin nhắn:", otp_data.get("SmsContent"))
            print("===============================")
            
else:
    print("Không tìm thấy biến môi trường 'viotp_token' trong file .env!")

