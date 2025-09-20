#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Working HTML Email Test Script
"""

import os
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import smtplib
import ssl
from datetime import datetime
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Email Configuration
EMAIL_HOST = os.getenv("EMAIL_HOST")
EMAIL_PORT = int(os.getenv("EMAIL_PORT", 587))
EMAIL_USER = os.getenv("EMAIL_HOST_USER")
EMAIL_PASSWORD = os.getenv("EMAIL_HOST_PASSWORD")
DEFAULT_FROM_EMAIL = os.getenv("DEFAULT_FROM_EMAIL", EMAIL_USER)
RECIPIENTS = ["vviiddaa2@gmail.com", "luisdavid313@gmail.com"]

# Simple HTML Template with inline styles (no CSS variables)
HTML_TEMPLATE = """
<html>
<body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333; max-width: 600px; margin: 0 auto; padding: 20px;">
    <div style="background-color: #d32f2f; color: white; padding: 20px; text-align: center; border-radius: 5px 5px 0 0;">
        <h1 style="margin: 0;">🔫 Shooting Lesson Confirmation</h1>
    </div>
    
    <div style="padding: 20px; background-color: #fff; border-left: 1px solid #eee; border-right: 1px solid #eee;">
        <p>Hello <strong>VIP Member</strong>,</p>
        
        <p>Your shooting lesson has been confirmed with these details:</p>
        
        <div style="background-color: #f9f9f9; padding: 15px; border-radius: 5px; margin: 20px 0; border-left: 4px solid #d32f2f;">
            <h3 style="margin-top: 0;">Lesson Details</h3>
            <p><strong>Date & Time:</strong> {current_datetime}</p>
            <p><strong>Instructor:</strong> Luis David</p>
            <p><strong>Location:</strong> Premium Shooting Range</p>
            <p><strong>Reference:</strong> SR-{ref_number}</p>
        </div>
        
        <center>
            <a href="https://example.com/booking?ref=SR-{ref_number}" 
               style="display: inline-block; padding: 10px 20px; background-color: #d32f2f; color: white; text-decoration: none; border-radius: 5px; margin: 10px 0;">
                View Your Booking
            </a>
        </center>
        
        <p>Please arrive 15 minutes early for safety briefing.</p>
    </div>
    
    <div style="padding: 20px; text-align: center; font-size: 12px; color: #777; background-color: #f8f9fa; border-radius: 0 0 5px 5px;">
        <p>© {current_year} Ready Aim Learn. All rights reserved.</p>
    </div>
</body>
</html>
"""

def send_email():
    try:
        # ساختن ایمیل
        msg = MIMEMultipart('alternative')
        msg['Subject'] = "🔫 Your Shooting Lesson Confirmation"
        msg['From'] = DEFAULT_FROM_EMAIL
        msg['To'] = ", ".join(RECIPIENTS)

        ref_number = datetime.now().strftime("%Y%m%d%H%M")
        current_datetime = datetime.now().strftime("%A, %B %d, %Y at %I:%M %p")
        current_year = datetime.now().year

        text_content = f"""Shooting Lesson Confirmation
        -------------------------------
        Date/Time: {current_datetime}
        Instructor: Luis David
        Location: Premium Shooting Range
        Reference: SR-{ref_number}

        Please arrive 15 minutes early.
        """

        html_content = HTML_TEMPLATE.format(
            current_datetime=current_datetime,
            current_year=current_year,
            ref_number=ref_number
        )

        msg.attach(MIMEText(text_content, 'plain'))
        msg.attach(MIMEText(html_content, 'html'))

        # مرحله به مرحله دیباگ
        print("🔎 Connecting to server...")
        context = ssl.create_default_context()
        with smtplib.SMTP(EMAIL_HOST, EMAIL_PORT, timeout=20) as server:
            print("✅ Connected to host")

            code, response = server.ehlo()
            print(f"EHLO response: {code} {response.decode()}")

            print("🔎 Starting TLS...")
            server.starttls(context=context)
            code, response = server.ehlo()
            print(f"EHLO after STARTTLS: {code} {response.decode()}")

            print("🔎 Logging in...")
            server.login(EMAIL_USER, EMAIL_PASSWORD)
            print("✅ Logged in successfully")

            server.sendmail(DEFAULT_FROM_EMAIL, RECIPIENTS, msg.as_string())
            print("✅ Email successfully sent!")

    except Exception as e:
        print("❌ Error during email process:", repr(e))

if __name__ == "__main__":
    print("\n" + "="*50)
    print("🔫 Shooting Range Email Test")
    print("="*50)
    
    # Verify configuration
    print(f"SMTP Server: {EMAIL_HOST}:{EMAIL_PORT}")
    print(f"From: {DEFAULT_FROM_EMAIL}")
    print(f"To: {', '.join(RECIPIENTS)}")
    
    send_email()
    
    print("\n" + "="*50)
    print("Test completed")