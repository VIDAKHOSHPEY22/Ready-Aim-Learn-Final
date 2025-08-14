import os
import django
from django.core.mail import send_mail, EmailMultiAlternatives
from dotenv import load_dotenv
from pathlib import Path

# Load environment variables from .env
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

# Set Django settings
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ready_aim_learn.settings")
django.setup()

# Ensure your email settings are correct in settings.py or override them here
from django.conf import settings
if not settings.configured:
    settings.configure(
        EMAIL_BACKEND='django.core.mail.backends.smtp.EmailBackend',
        EMAIL_HOST=os.getenv("EMAIL_HOST", "smtp.gmail.com"),
        EMAIL_PORT=int(os.getenv("EMAIL_PORT", 587)),
        EMAIL_USE_TLS=True,
        EMAIL_HOST_USER=os.getenv("EMAIL_HOST_USER"),
        EMAIL_HOST_PASSWORD=os.getenv("EMAIL_HOST_PASSWORD"),  # Gmail App Password
        DEFAULT_FROM_EMAIL=os.getenv("EMAIL_HOST_USER"),
    )

# Send test email
try:
    send_mail(
        "Test Email",
        "This is a test message from Django.",
        os.getenv("EMAIL_HOST_USER"),
        ["vviiddaa2@gmail.com"],  # Replace
        fail_silently=False,
    )
    print("Email sent successfully!")
except Exception as e:
    print("Error sending email:", e)
