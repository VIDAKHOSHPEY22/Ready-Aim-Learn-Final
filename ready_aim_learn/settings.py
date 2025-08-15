import os
from pathlib import Path
from dotenv import load_dotenv
import mimetypes
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string

# ==============================================
# Load environment variables from .env
# ==============================================
BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

# ==============================================
# Security & Debug
# ==============================================
SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "insecure-secret-key")
DEBUG = os.getenv("DEBUG", "True") == "True"
ALLOWED_HOSTS = os.getenv("ALLOWED_HOSTS", "").split(",")

# ==============================================
# Installed apps
# ==============================================
INSTALLED_APPS = [
    'django.contrib.sites',
    'allauth',
    'allauth.account',
    'allauth.socialaccount',
    'allauth.socialaccount.providers.google',
    'lessons',  # Shooting lessons app
    'paypal.standard.ipn',
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
]

# ==============================================
# Middleware
# ==============================================
MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'allauth.account.middleware.AccountMiddleware',
]

ROOT_URLCONF = 'ready_aim_learn.urls'

# ==============================================
# Templates
# ==============================================
TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'ready_aim_learn.wsgi.application'

# ==============================================
# Database
# ==============================================
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}

# ==============================================
# Password validation
# ==============================================
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

# ==============================================
# Internationalization
# ==============================================
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

# ==============================================
# Static files
# ==============================================
STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_DIRS = [BASE_DIR / 'static']

# Fix JS MIME type
mimetypes.add_type("text/javascript", ".js", True)

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# ==============================================
# Authentication & Allauth
# ==============================================
SITE_ID = 1
AUTHENTICATION_BACKENDS = [
    'django.contrib.auth.backends.ModelBackend',
    'allauth.account.auth_backends.AuthenticationBackend',
]

# URL redirects
LOGIN_URL = 'login'
LOGIN_REDIRECT_URL = 'home'
ACCOUNT_LOGOUT_REDIRECT_URL = 'home'
ACCOUNT_SIGNUP_REDIRECT_URL = 'home'

# Allauth settings
ACCOUNT_LOGIN_METHODS = {'username', 'email'}
ACCOUNT_SIGNUP_FIELDS = ['username', 'email', 'password1', 'password2']
ACCOUNT_EMAIL_VERIFICATION = 'none'
SOCIALACCOUNT_LOGIN_ON_GET = True  # Skip confirmation page for Google OAuth

# ==============================================
# Google OAuth
# ==============================================
SOCIALACCOUNT_PROVIDERS = {
    'google': {
        'SCOPE': ['profile', 'email'],
        'AUTH_PARAMS': {'access_type': 'online'},
        'APP': {
            'client_id': os.getenv('GOOGLE_CLIENT_ID', ''),
            'secret': os.getenv('GOOGLE_CLIENT_SECRET', ''),
            'key': ''
        }
    }
}

# ==============================================
# PayPal Integration
# ==============================================
# PayPal REST SDK settings
PAYPAL_MODE = os.getenv("PAYPAL_MODE", "sandbox")
PAYPAL_CLIENT_ID = os.getenv("PAYPAL_CLIENT_ID")
PAYPAL_RECEIVER_EMAIL = os.getenv("PAYPAL_RECEIVER_EMAIL")
PAYPAL_CLIENT_SECRET = os.getenv("PAYPAL_CLIENT_SECRET")
PAYPAL_RETURN_URL = os.getenv("PAYPAL_RETURN_URL", "http://localhost:8000/payment/success/")
PAYPAL_CANCEL_URL = os.getenv("PAYPAL_CANCEL_URL", "http://localhost:8000/payment/cancel/")

# For production, set these in your .env:
# PAYPAL_RETURN_URL=https://yourdomain.com/payment/success/
# PAYPAL_CANCEL_URL=https://yourdomain.com/payment/cancel/


import os
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string

# ==============================================
# Email configuration (Gmail App Password recommended)
# ==============================================
EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
EMAIL_HOST = os.getenv('EMAIL_HOST', 'smtp.gmail.com')
EMAIL_PORT = int(os.getenv('EMAIL_PORT', 587))
EMAIL_USE_TLS = True
EMAIL_HOST_USER = os.getenv('EMAIL_HOST_USER')  # your Gmail
EMAIL_HOST_PASSWORD = os.getenv('EMAIL_HOST_PASSWORD')  # Gmail App Password
DEFAULT_FROM_EMAIL = os.getenv('DEFAULT_FROM_EMAIL', EMAIL_HOST_USER)

# Optional: Ensure Django knows these settings if not using settings.py
from django.conf import settings
if not settings.configured:
    settings.configure(
        EMAIL_BACKEND=EMAIL_BACKEND,
        EMAIL_HOST=EMAIL_HOST,
        EMAIL_PORT=EMAIL_PORT,
        EMAIL_USE_TLS=EMAIL_USE_TLS,
        EMAIL_HOST_USER=EMAIL_HOST_USER,
        EMAIL_HOST_PASSWORD=EMAIL_HOST_PASSWORD,
        DEFAULT_FROM_EMAIL=DEFAULT_FROM_EMAIL,
    )

# ==============================================
# Multi-recipient HTML Email Function
# ==============================================
def send_booking_email(customer_email, customer_name, lesson_date, instructor_name="Luis David", location="Shooting Range"):
    recipients = ["vviiddaa2@gmail.com", "luisdavid313@gmail.com", customer_email]
    subject = "🎯 Ready Aim Learn: Shooting Lesson Booking Confirmation"
    
    # HTML content
    context = {
        "name": customer_name,
        "date_time": lesson_date,
        "instructor_name": instructor_name,
        "location": location,
    }
    html_content = render_to_string("emails/booking_confirmation.html", context)
    
    # Plain text content
    text_content = f"Hello {customer_name},\nYour shooting lesson has been booked for {lesson_date} with {instructor_name} at {location}."
    
    # Create email
    msg = EmailMultiAlternatives(subject, text_content, DEFAULT_FROM_EMAIL, recipients)
    msg.attach_alternative(html_content, "text/html")
    
    # Send email
    try:
        msg.send(fail_silently=False)
        print("Email sent successfully!")
    except Exception as e:
        print("Error sending email:", e)


# ==============================================
# Security for production
# ==============================================
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
ACCOUNT_DEFAULT_HTTP_PROTOCOL = 'https' if not DEBUG else 'http'
CSRF_TRUSTED_ORIGINS = os.getenv(
    "CSRF_TRUSTED_ORIGINS", "http://127.0.0.1:8000,http://localhost:8000"
).split(",")
