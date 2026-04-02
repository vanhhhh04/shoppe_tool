print("hello world")
# this file is for testing the automation fetch number from viotp and check if the phone number is valid or not 
import os
from dotenv import load_dotenv
load_dotenv()

viotp_token = os.getenv("viotp_token")
print(viotp_token)
