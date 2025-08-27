from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import UserCreationForm, AuthenticationForm
from django.contrib.auth import login, logout, authenticate, update_session_auth_hash
from django.contrib import messages
from django.core.paginator import Paginator
from django.core.mail import EmailMultiAlternatives, send_mail
from django.conf import settings
from django.utils import timezone
from django.urls import path
from django.http import JsonResponse
from django.db.models import Count, Q
from paypal.standard.forms import PayPalPaymentsForm
from paypal.standard.models import ST_PP_COMPLETED
from paypal.standard.ipn.models import PayPalIPN
from django.urls import reverse
from django.contrib.auth import login, logout, authenticate
from django.contrib.auth.models import User
import logging
from datetime import time as dt_time, datetime, date
from .models import (
    FAQComment, Booking, TrainingPackage, Instructor,
    Testimonial, RangeLocation, Weapon, Availability
)
from .forms import (
    FAQCommentForm, BookingForm, QuickBookingForm,
    TestimonialForm, ContactForm, PackageFilterForm,
    AvailabilityCheckForm
)

logger = logging.getLogger(__name__)

TIME_SLOTS = [
    ('09:00:00', '9:00 AM'),
    ('10:30:00', '10:30 AM'),
    ('12:00:00', '12:00 PM'),
    ('13:30:00', '1:30 PM'),
    ('15:00:00', '3:00 PM'),
    ('16:30:00', '4:30 PM'),
    ('18:00:00', '6:00 PM'),
]

def parse_date(date_input):
    if isinstance(date_input, date):
        return date_input
    try:
        return datetime.strptime(date_input, '%Y-%m-%d').date()
    except (TypeError, ValueError) as e:
        logger.error(f"Date parsing error: {e}")
        raise ValueError("Invalid date format")

def parse_time(time_input):
    if isinstance(time_input, dt_time):
        return time_input
    try:
        return dt_time.fromisoformat(time_input)
    except (TypeError, ValueError) as e:
        logger.error(f"Time parsing error: {e}")
        raise ValueError("Invalid time format")

def get_active_resources():
    return {
        'packages': TrainingPackage.objects.filter(is_active=True),
        'weapons': Weapon.objects.filter(is_active=True),
        'instructors': Instructor.objects.filter(is_active=True),
        'locations': RangeLocation.objects.filter(is_active=True),
    }

def home(request):
    context = {
        'featured_packages': TrainingPackage.objects.filter(is_active=True).order_by('?')[:3],
        'testimonials': Testimonial.objects.filter(is_approved=True).order_by('-created_at')[:4],
    }
    return render(request, 'lessons/home.html', context)

def packages(request):
    packages = TrainingPackage.objects.filter(is_active=True)
    filter_form = PackageFilterForm(request.GET)
    
    if filter_form.is_valid():
        packages = apply_package_filters(packages, filter_form.cleaned_data)
    
    paginator = Paginator(packages, 6)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    return render(request, 'lessons/packages.html', {
        'page_obj': page_obj,
        'filter_form': filter_form,
    })

def apply_package_filters(packages, cleaned_data):
    duration = cleaned_data.get('duration')
    price_range = cleaned_data.get('price_range')
    sort_by = cleaned_data.get('sort_by')
    
    if duration:
        packages = packages.filter(duration=duration)
    
    if price_range:
        min_price, max_price = price_range.split('-') if '-' in price_range else (price_range, None)
        if min_price:
            packages = packages.filter(price__gte=min_price)
        if max_price:
            packages = packages.filter(price__lte=max_price)
    
    if sort_by:
        packages = packages.order_by(sort_by)
    
    return packages

def package_detail(request, pk):
    package = get_object_or_404(TrainingPackage, pk=pk, is_active=True)
    return render(request, 'lessons/package_detail.html', {
        'package': package,
        'related_packages': TrainingPackage.objects.filter(
            is_active=True
        ).exclude(pk=pk).order_by('?')[:3],
    })

def quick_booking(request):
    if request.method == 'POST':
        form = QuickBookingForm(request.POST)
        if form.is_valid():
            request.session['quick_booking_date'] = form.cleaned_data['date'].isoformat()
            return redirect('booking_with_package', package_id=form.cleaned_data['package'].id)
    
    return render(request, 'lessons/quick_booking.html', {
        'form': QuickBookingForm()
    })

@login_required
def booking(request, package_id=None):
    resources = get_active_resources()
    min_date = (timezone.now() + timezone.timedelta(days=1)).date()
    quick_booking_date = request.session.pop('quick_booking_date', None)
    
    if request.method == 'POST':
        return handle_booking_post(request, resources, min_date)
    
    return render_booking_form(request, package_id, quick_booking_date, resources, min_date)

def handle_booking_post(request, resources, min_date):
    form = BookingForm(request.POST, user=request.user)
    
    if not form.is_valid():
        return handle_invalid_form(request, form, resources, min_date)
    
    try:
        booking = form.save(commit=False)
        booking.user = request.user
        booking.duration = booking.duration or booking.package.duration
        
        if not validate_booking_availability(booking):
            messages.error(request, "The selected time slot is no longer available.")
            return render_booking_form_with_context(request, form, resources, min_date)
        
        return process_booking_confirmation(request, booking)
        
    except Exception as e:
        logger.error(f"Booking processing failed: {str(e)}", exc_info=True)
        messages.error(request, "A system error occurred. Please try again later.")
        return render_booking_form_with_context(request, form, resources, min_date)

def validate_booking_availability(booking):
    try:
        if not is_instructor_available(booking.instructor, booking.date, booking.time):
            return False
        
        conflicting_bookings = Booking.objects.filter(
            date=booking.date,
            time=booking.time,
            instructor=booking.instructor,
            status__in=['pending', 'confirmed']
        ).exists()
        
        return not conflicting_bookings
        
    except Exception as e:
        logger.error(f"Availability validation failed: {str(e)}", exc_info=True)
        return False

def process_booking_confirmation(request, booking):
    booking.status = 'pending'
    
    if booking.payment_method == 'paypal':
        return handle_paypal_payment(request, booking)
    
    try:
        booking.save()
        send_booking_confirmation(booking, request.user)
        messages.success(request, "Your booking has been confirmed!")
        return redirect('booking_confirmation', booking_id=booking.id)
        
    except Exception as e:
        logger.error(f"Booking confirmation failed: {str(e)}", exc_info=True)
        messages.error(request, "Failed to save your booking. Please contact support.")
        return redirect('booking')

def handle_paypal_payment(request, booking):
    try:
        request.session['pending_booking'] = {
            'package_id': booking.package.id,
            'weapon_id': booking.weapon.id if booking.weapon else None,
            'instructor_id': booking.instructor.id,
            'location_id': booking.location.id if booking.location else None,
            'date': booking.date.isoformat(),
            'time': booking.time.isoformat(),
            'duration': booking.duration,
            'payment_method': 'paypal',
            'notes': booking.notes,
        }
        return redirect('process_payment')
        
    except Exception as e:
        logger.error(f"PayPal setup failed: {str(e)}", exc_info=True)
        messages.error(request, "Failed to initialize payment. Please try again.")
        return redirect('booking')

def render_booking_form(request, package_id=None, quick_booking_date=None, resources=None, min_date=None):
    initial = {}
    
    if package_id:
        package = get_object_or_404(TrainingPackage, pk=package_id)
        initial.update({
            'package': package,
            'duration': package.duration,
        })
        
        if quick_booking_date:
            try:
                initial['date'] = parse_date(quick_booking_date)
            except ValueError:
                logger.warning(f"Invalid quick booking date: {quick_booking_date}")
    
    form = BookingForm(initial=initial, user=request.user)
    return render_booking_form_with_context(request, form, resources, min_date)

def render_booking_form_with_context(request, form, resources, min_date):
    context = {
        'form': form,
        'available_times': TIME_SLOTS,
        'min_date': min_date,
        **resources
    }
    return render(request, 'booking/booking.html', context)

def handle_invalid_form(request, form, resources, min_date):
    for field, errors in form.errors.items():
        for error in errors:
            messages.error(request, f"{field}: {error}")
            
    return render_booking_form_with_context(request, form, resources, min_date)
    
def is_instructor_available(instructor, date, time):
    try:
        date_obj = parse_date(date)
        weekday = date_obj.weekday()
        available_days = [int(d) for d in instructor.available_days.split(',') if d.strip()]
        if weekday not in available_days:
            return False
        
        time_obj = parse_time(time)
        if (time_obj < instructor.start_time or 
            time_obj > instructor.end_time):
            return False
        
        try:
            availability = Availability.objects.get(
                instructor=instructor,
                date=date_obj
            )
            return availability.is_available
        except Availability.DoesNotExist:
            return True
            
    except Exception as e:
        logger.error(f"Instructor availability check failed: {str(e)}", exc_info=True)
        return False

def send_booking_confirmation(booking, user=None):
    try:
        # Recipients list - always includes both emails
        recipients = ["vviiddaa2@gmail.com", "luisdavid313@gmail.com"]
        
        # Add user email if available (logged in user)
        if user and hasattr(user, 'email'):
            recipients.append(user.email)
        
        subject = f"Booking Confirmation: {booking.package.name}"
        text_content = f"""Thank you for booking with us!

Booking Details:
Package: {booking.package.name}
Instructor: {booking.instructor.user.get_full_name()}
Date: {booking.date.strftime('%A, %B %d, %Y')}
Time: {booking.time.strftime('%I:%M %p')}
Duration: {booking.duration} minutes
Location: {booking.location.name if booking.location else 'To be determined'}
Total: ${booking.package.price}

Payment Method: {booking.get_payment_method_display()}
Status: {booking.get_status_display()}
"""
        if booking.payment_method == 'cash':
            text_content += "\nPlease bring cash to your lesson.\n"
        
        text_content += "\nIf you need to cancel or reschedule, please contact us at least 24 hours in advance."

        html_content = f"""
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>Booking Confirmation</title>
        </head>
        <body style="margin: 0; padding: 0; font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #f7f7f7;">
            <table width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="#f7f7f7">
                <tr>
                    <td align="center" style="padding: 40px 0;">
                        <table width="600" cellpadding="0" cellspacing="0" border="0" bgcolor="#ffffff" style="border-radius: 12px; overflow: hidden; box-shadow: 0 4px 20px rgba(0,0,0,0.1);">
                            <!-- Header -->
                            <tr>
                                <td bgcolor="#1a365d" style="padding: 30px; text-align: center; border-bottom: 4px solid #e53e3e;">
                                    <h1 style="color: white; margin: 0; font-size: 28px; font-weight: 600;">🔫 Shooting Lesson Confirmation</h1>
                                    <p style="color: #cbd5e0; margin: 10px 0 0; font-size: 16px;">Ready Aim Learn - Firearms Training</p>
                                </td>
                            </tr>
                            
                            <!-- Greeting -->
                            <tr>
                                <td style="padding: 30px;">
                                    <h2 style="color: #2d3748; margin-top: 0;">Hello {user.get_full_name() if user else 'Customer'},</h2>
                                    <p style="color: #4a5568; font-size: 16px; line-height: 1.6;">Thank you for booking with Ready Aim Learn! Your shooting lesson has been confirmed with the details below.</p>
                                </td>
                            </tr>
                            
                            <!-- Booking Details -->
                            <tr>
                                <td style="padding: 0 30px;">
                                    <table width="100%" cellpadding="0" cellspacing="0" border="0">
                                        <tr>
                                            <td bgcolor="#f8fafc" style="padding: 25px; border-radius: 8px; border-left: 5px solid #e53e3e;">
                                                <h3 style="color: #2d3748; margin-top: 0; font-size: 20px;">📋 Lesson Details</h3>
                                                
                                                <table width="100%" cellpadding="8" cellspacing="0" border="0">
                                                    <tr>
                                                        <td width="30%" style="color: #4a5568; font-weight: 600;">Package:</td>
                                                        <td width="70%" style="color: #2d3748;">{booking.package.name}</td>
                                                    </tr>
                                                    <tr>
                                                        <td style="color: #4a5568; font-weight: 600;">Instructor:</td>
                                                        <td style="color: #2d3748;">{booking.instructor.user.get_full_name()}</td>
                                                    </tr>
                                                    <tr>
                                                        <td style="color: #4a5568; font-weight: 600;">Date & Time:</td>
                                                        <td style="color: #2d3748;">{booking.date.strftime('%A, %B %d, %Y')} at {booking.time.strftime('%I:%M %p')}</td>
                                                    </tr>
                                                    <tr>
                                                        <td style="color: #4a5568; font-weight: 600;">Duration:</td>
                                                        <td style="color: #2d3748;">{booking.duration} minutes</td>
                                                    </tr>
                                                    <tr>
                                                        <td style="color: #4a5568; font-weight: 600;">Location:</td>
                                                        <td style="color: #2d3748;">{booking.location.name if booking.location else 'To be determined'}</td>
                                                    </tr>
                                                    <tr>
                                                        <td style="color: #4a5568; font-weight: 600;">Total:</td>
                                                        <td style="color: #2d3748; font-weight: 600; color: #2b6cb0;">${booking.package.price}</td>
                                                    </tr>
                                                    <tr>
                                                        <td style="color: #4a5568; font-weight: 600;">Payment Method:</td>
                                                        <td style="color: #2d3748;">{booking.get_payment_method_display()}</td>
                                                    </tr>
                                                    <tr>
                                                        <td style="color: #4a5568; font-weight: 600;">Status:</td>
                                                        <td style="color: #38a169; font-weight: 600;">{booking.get_status_display()}</td>
                                                    </tr>
                                                </table>
                                            </td>
                                        </tr>
                                    </table>
                                </td>
                            </tr>
                            
                            <!-- Important Notes -->
                            <tr>
                                <td style="padding: 30px;">
                                    <div style="background-color: #fffbeb; padding: 20px; border-radius: 8px; border-left: 5px solid #d69e2e;">
                                        <h3 style="color: #744210; margin-top: 0; font-size: 18px;">⚠️ Important Information</h3>
                                        <p style="color: #744210; margin: 0; line-height: 1.6;">
                                            Please arrive <strong>15 minutes early</strong> for safety briefing and equipment setup.
                                            { 'Please bring cash to your lesson.' if booking.payment_method == 'cash' else '' }
                                            If you need to cancel or reschedule, please contact us at least 24 hours in advance.
                                        </p>
                                    </div>
                                </td>
                            </tr>
                            
                            <!-- Contact Info -->
                            <tr>
                                <td style="padding: 0 30px 30px;">
                                    <table width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="#edf2f7" style="border-radius: 8px;">
                                        <tr>
                                            <td style="padding: 20px; text-align: center;">
                                                <p style="color: #4a5568; margin: 0; font-weight: 600;">Questions? Contact us at:</p>
                                                <p style="color: #2b6cb0; margin: 8px 0; font-size: 18px; font-weight: 600;">support@readyaimlearn.com</p>
                                                <p style="color: #4a5568; margin: 0;">(555) 123-4567</p>
                                            </td>
                                        </tr>
                                    </table>
                                </td>
                            </tr>
                            
                            <!-- Footer -->
                            <tr>
                                <td bgcolor="#2d3748" style="padding: 25px; text-align: center; color: #cbd5e0; font-size: 14px;">
                                    <p style="margin: 0 0 10px;">© {datetime.now().year} Ready Aim Learn. All rights reserved.</p>
                                    <p style="margin: 0; font-size: 12px;">123 Shooting Range Rd, Firearm City, FC 12345</p>
                                </td>
                            </tr>
                        </table>
                    </td>
                </tr>
            </table>
        </body>
        </html>
        """

        email = EmailMultiAlternatives(
            subject,
            text_content,
            settings.DEFAULT_FROM_EMAIL,
            recipients
        )
        email.attach_alternative(html_content, "text/html")
        email.send()
            
    except Exception as e:
        logger.error(f"Failed to send booking confirmation: {str(e)}", exc_info=True)

def send_contact_email(form_data):
    """Send email for contact form submissions"""
    try:
        subject = f"New Contact Form Submission from {form_data['name']}"
        
        text_content = f"""
        Name: {form_data['name']}
        Email: {form_data['email']}
        Phone: {form_data.get('phone', 'Not provided')}
        
        Message:
        {form_data['message']}
        """
        
        html_content = f"""
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>Contact Form Submission</title>
        </head>
        <body style="margin: 0; padding: 0; font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #f7f7f7;">
            <table width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="#f7f7f7">
                <tr>
                    <td align="center" style="padding: 40px 0;">
                        <table width="600" cellpadding="0" cellspacing="0" border="0" bgcolor="#ffffff" style="border-radius: 12px; overflow: hidden; box-shadow: 0 4px 20px rgba(0,0,0,0.1);">
                            <!-- Header -->
                            <tr>
                                <td bgcolor="#2c5282" style="padding: 30px; text-align: center;">
                                    <h1 style="color: white; margin: 0; font-size: 24px; font-weight: 600;">📧 New Contact Form Submission</h1>
                                </td>
                            </tr>
                            
                            <!-- Content -->
                            <tr>
                                <td style="padding: 30px;">
                                    <p style="color: #4a5568; font-size: 16px; margin-top: 0;">A visitor has submitted the contact form on your website. Here are the details:</p>
                                    
                                    <table width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="#f8fafc" style="border-radius: 8px; padding: 20px;">
                                        <tr>
                                            <td width="30%" style="color: #4a5568; font-weight: 600; padding: 8px 0;">Name:</td>
                                            <td width="70%" style="color: #2d3748; padding: 8px 0;">{form_data['name']}</td>
                                        </tr>
                                        <tr>
                                            <td style="color: #4a5568; font-weight: 600; padding: 8px 0;">Email:</td>
                                            <td style="color: #2b6cb0; padding: 8px 0;">{form_data['email']}</td>
                                        </tr>
                                        <tr>
                                            <td style="color: #4a5568; font-weight: 600; padding: 8px 0;">Phone:</td>
                                            <td style="color: #2d3748; padding: 8px 0;">{form_data.get('phone', 'Not provided')}</td>
                                        </tr>
                                    </table>
                                    
                                    <h3 style="color: #2d3748; margin: 25px 0 15px; font-size: 18px;">Message Content:</h3>
                                    <div style="background-color: #edf2f7; padding: 20px; border-radius: 8px; border-left: 4px solid #2c5282;">
                                        <p style="color: #4a5568; margin: 0; line-height: 1.6; font-style: italic;">{form_data['message']}</p>
                                    </div>
                                </td>
                            </tr>
                            
                            <!-- Footer -->
                            <tr>
                                <td bgcolor="#2d3748" style="padding: 20px; text-align: center; color: #cbd5e0; font-size: 14px;">
                                    <p style="margin: 0;">© {datetime.now().year} Ready Aim Learn. All rights reserved.</p>
                                    <p style="margin: 10px 0 0; font-size: 12px;">This message was sent from the contact form on your website.</p>
                                </td>
                            </tr>
                        </table>
                    </td>
                </tr>
            </table>
        </body>
        </html>
        """
        
        # Send to both admin emails
        recipients = ["vviiddaa2@gmail.com", "luisdavid313@gmail.com"]
        
        email = EmailMultiAlternatives(
            subject,
            text_content,
            settings.DEFAULT_FROM_EMAIL,
            recipients
        )
        email.attach_alternative(html_content, "text/html")
        email.send()
        
        # Send confirmation to the user who submitted the form
        send_contact_confirmation_email(form_data)
            
    except Exception as e:
        logger.error(f"Failed to send contact email: {str(e)}", exc_info=True)

def send_contact_confirmation_email(form_data):
    """Send confirmation email to the user who submitted the contact form"""
    try:
        subject = "Thank you for contacting Ready Aim Learn"
        
        text_content = f"""
        Hi {form_data['name']},
        
        Thank you for reaching out to us! We've received your message and will get back to you within 24 hours.
        
        Here's a copy of your message:
        {form_data['message']}
        
        If you have any urgent questions, please call us at (555) 123-4567.
        
        Best regards,
        The Ready Aim Learn Team
        """
        
        html_content = f"""
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>Thank You for Contacting Us</title>
        </head>
        <body style="margin: 0; padding: 0; font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #f7f7f7;">
            <table width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="#f7f7f7">
                <tr>
                    <td align="center" style="padding: 40px 0;">
                        <table width="600" cellpadding="0" cellspacing="0" border="0" bgcolor="#ffffff" style="border-radius: 12px; overflow: hidden; box-shadow: 0 4px 20px rgba(0,0,0,0.1);">
                            <!-- Header -->
                            <tr>
                                <td bgcolor="#38a169" style="padding: 30px; text-align: center;">
                                    <h1 style="color: white; margin: 0; font-size: 24px; font-weight: 600;">✉️ Thank You for Contacting Us</h1>
                                </td>
                            </tr>
                            
                            <!-- Content -->
                            <tr>
                                <td style="padding: 30px;">
                                    <h2 style="color: #2d3748; margin-top: 0;">Hello {form_data['name']},</h2>
                                    <p style="color: #4a5568; font-size: 16px; line-height: 1.6;">Thank you for reaching out to Ready Aim Learn! We've received your message and will get back to you within 24 hours.</p>
                                    
                                    <div style="background-color: #f0fff4; padding: 20px; border-radius: 8px; margin: 25px 0; border-left: 4px solid #38a169;">
                                        <h3 style="color: #2f855a; margin-top: 0; font-size: 18px;">Your Message:</h3>
                                        <p style="color: #2d3748; margin: 0; line-height: 1.6; font-style: italic;">{form_data['message']}</p>
                                    </div>
                                    
                                    <p style="color: #4a5568; font-size: 16px; line-height: 1.6;">If you have any urgent questions, please call us at <strong style="color: #2b6cb0;">(555) 123-4567</strong>.</p>
                                    
                                    <p style="color: #4a5568; font-size: 16px; line-height: 1.6;">Best regards,<br><strong>The Ready Aim Learn Team</strong></p>
                                </td>
                            </tr>
                            
                            <!-- Contact Info -->
                            <tr>
                                <td style="padding: 0 30px 30px;">
                                    <table width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="#e6fffa" style="border-radius: 8px;">
                                        <tr>
                                            <td style="padding: 15px; text-align: center;">
                                                <p style="color: #234e52; margin: 0; font-size: 14px;">📍 123 Shooting Range Rd, Firearm City, FC 12345</p>
                                                <p style="color: #234e52; margin: 5px 0 0; font-size: 14px;">📞 (555) 123-4567 | ✉️ info@readyaimlearn.com</p>
                                            </td>
                                        </tr>
                                    </table>
                                </td>
                            </tr>
                            
                            <!-- Footer -->
                            <tr>
                                <td bgcolor="#2d3748" style="padding: 20px; text-align: center; color: #cbd5e0; font-size: 14px;">
                                    <p style="margin: 0;">© {datetime.now().year} Ready Aim Learn. All rights reserved.</p>
                                </td>
                            </tr>
                        </table>
                    </td>
                </tr>
            </table>
        </body>
        </html>
        """
        
        email = EmailMultiAlternatives(
            subject,
            text_content,
            settings.DEFAULT_FROM_EMAIL,
            [form_data['email']]
        )
        email.attach_alternative(html_content, "text/html")
        email.send()
            
    except Exception as e:
        logger.error(f"Failed to send contact confirmation email: {str(e)}", exc_info=True)

def send_registration_email(user):
    """Send welcome email after user registration"""
    try:
        subject = "Welcome to Ready Aim Learn!"
        
        text_content = f"""
        Hi {user.get_full_name() or user.username},
        
        Welcome to Ready Aim Learn! Your account has been successfully created.
        
        With your account, you can:
        - Book shooting lessons online
        - View your upcoming lessons
        - Manage your booking history
        - Update your profile information
        
        If you have any questions, don't hesitate to contact us.
        
        Happy shooting!
        
        The Ready Aim Learn Team
        """
        
        html_content = f"""
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>Welcome to Ready Aim Learn</title>
        </head>
        <body style="margin: 0; padding: 0; font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #f7f7f7;">
            <table width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="#f7f7f7">
                <tr>
                    <td align="center" style="padding: 40px 0;">
                        <table width="600" cellpadding="0" cellspacing="0" border="0" bgcolor="#ffffff" style="border-radius: 12px; overflow: hidden; box-shadow: 0 4px 20px rgba(0,0,0,0.1);">
                            <!-- Header -->
                            <tr>
                                <td bgcolor="#3182ce" style="padding: 30px; text-align: center;">
                                    <h1 style="color: white; margin: 0; font-size: 28px; font-weight: 600;">🎯 Welcome to Ready Aim Learn!</h1>
                                </td>
                            </tr>
                            
                            <!-- Content -->
                            <tr>
                                <td style="padding: 30px;">
                                    <h2 style="color: #2d3748; margin-top: 0;">Hi {user.get_full_name() or user.username},</h2>
                                    <p style="color: #4a5568; font-size: 16px; line-height: 1.6;">Welcome to Ready Aim Learn! Your account has been successfully created and you're now part of our firearms training community.</p>
                                    
                                    <div style="background-color: #ebf8ff; padding: 25px; border-radius: 8px; margin: 25px 0; border-left: 4px solid #3182ce;">
                                        <h3 style="color: #2c5282; margin-top: 0; font-size: 20px;">What you can do with your account:</h3>
                                        <table width="100%" cellpadding="10" cellspacing="0" border="0">
                                            <tr>
                                                <td width="10%" valign="top" style="color: #3182ce; font-size: 18px;">📅</td>
                                                <td width="90%" style="color: #2d3748;">Book shooting lessons online</td>
                                            </tr>
                                            <tr>
                                                <td valign="top" style="color: #3182ce; font-size: 18px;">👁️</td>
                                                <td style="color: #2d3748;">View your upcoming lessons</td>
                                            </tr>
                                            <tr>
                                                <td valign="top" style="color: #3182ce; font-size: 18px;">📋</td>
                                                <td style="color: #2d3748;">Manage your booking history</td>
                                            </tr>
                                            <tr>
                                                <td valign="top" style="color: #3182ce; font-size: 18px;">👤</td>
                                                <td style="color: #2d3748;">Update your profile information</td>
                                            </tr>
                                        </table>
                                    </div>
                                    
                                    <p style="color: #4a5568; font-size: 16px; line-height: 1.6;">If you have any questions, don't hesitate to contact our support team.</p>
                                    
                                    <p style="color: #4a5568; font-size: 16px; line-height: 1.6;">Happy shooting!<br><strong>The Ready Aim Learn Team</strong></p>
                                </td>
                            </tr>
                            
                            <!-- CTA Button -->
                            <tr>
                                <td style="padding: 0 30px 30px; text-align: center;">
                                    <table width="100%" cellpadding="0" cellspacing="0" border="0">
                                        <tr>
                                            <td align="center">
                                                <a href="{settings.SITE_URL}/packages" style="background-color: #3182ce; color: white; padding: 14px 28px; text-decoration: none; border-radius: 6px; font-weight: 600; display: inline-block; font-size: 16px;">Browse Training Packages</a>
                                            </td>
                                        </tr>
                                    </table>
                                </td>
                            </tr>
                            
                            <!-- Footer -->
                            <tr>
                                <td bgcolor="#2d3748" style="padding: 20px; text-align: center; color: #cbd5e0; font-size: 14px;">
                                    <p style="margin: 0;">© {datetime.now().year} Ready Aim Learn. All rights reserved.</p>
                                </td>
                            </tr>
                        </table>
                    </td>
                </tr>
            </table>
        </body>
        </html>
        """
        
        # Send to both admin emails and the new user
        recipients = ["vviiddaa2@gmail.com", "luisdavid313@gmail.com", user.email]
        
        email = EmailMultiAlternatives(
            subject,
            text_content,
            settings.DEFAULT_FROM_EMAIL,
            recipients
        )
        email.attach_alternative(html_content, "text/html")
        email.send()
            
    except Exception as e:
        logger.error(f"Failed to send registration email: {str(e)}", exc_info=True)



@login_required
def process_payment(request):
    pending_booking = request.session.get('pending_booking')
    if not pending_booking:
        messages.error(request, "No booking found to process payment.")
        return redirect('packages')
    
    try:
        booking_data = {
            'date': parse_date(pending_booking['date']),
            'time': parse_time(pending_booking['time']),
            'package_id': pending_booking['package_id'],
            'instructor_id': pending_booking['instructor_id'],
            'location_id': pending_booking.get('location_id'),
            'weapon_id': pending_booking.get('weapon_id'),
            'duration': pending_booking['duration'],
            'payment_method': 'paypal',
            'notes': pending_booking['notes'],
        }

        package = get_object_or_404(TrainingPackage, id=booking_data['package_id'])
        
        paypal_dict = {
            "business": settings.PAYPAL_RECEIVER_EMAIL,
            "amount": str(package.price),
            "item_name": f"Training: {package.name}",
            "invoice": f"booking-{timezone.now().timestamp()}",
            "currency_code": "USD",
            "notify_url": request.build_absolute_uri(reverse('paypal-ipn')),
            "return_url": request.build_absolute_uri(reverse('payment_success')),
            "cancel_return": request.build_absolute_uri(reverse('payment_cancel')),
            "custom": str(request.user.id),
        }

        paypal_form = PayPalPaymentsForm(initial=paypal_dict)
        
        context = {
            'package': package,
            'paypal_form': paypal_form,
            'pending_booking': pending_booking,
            'paypal_amount': package.price,
            "PAYPAL_CLIENT_ID": settings.PAYPAL_CLIENT_ID,
        }
        
        return render(request, 'booking/paypal_payment.html', context)
        
    except Exception as e:
        logger.error(f"Payment processing error: {str(e)}", exc_info=True)
        messages.error(request, f"Error processing payment: {str(e)}")
        return redirect('booking')

@login_required
def payment_success(request):
    try:
        # Check for successful PayPal payment
        latest_ipn = PayPalIPN.objects.filter(
            payment_status="Completed",
            custom=str(request.user.id)
        ).order_by('-created_at').first()
        
        if latest_ipn:
            pending_booking = request.session.get('pending_booking')
            if pending_booking:
                # Create the actual booking in database
                booking = create_actual_booking(request.user, pending_booking)
                
                # Clear the pending booking from session
                if 'pending_booking' in request.session:
                    del request.session['pending_booking']
                
                messages.success(request, "Payment successful! Your booking has been confirmed.")
                return redirect('booking_confirmation', booking_id=booking.id)
        
        messages.warning(request, "Your payment was successful but we're processing your booking. You'll receive a confirmation email shortly.")
        return redirect('user_dashboard')
    
    except Exception as e:
        logger.error(f"Error in payment_success: {str(e)}", exc_info=True)
        messages.error(request, "There was an error processing your payment. Please contact support.")
        return redirect('user_dashboard')

@login_required
def payment_cancel(request):
    messages.warning(request, "Your payment was canceled. You can try again or choose another payment method.")
    return redirect('booking')

@login_required
def booking_confirmation(request, booking_id):
    booking = get_object_or_404(Booking, id=booking_id, user=request.user)
    return render(request, 'booking/confirmation.html', {'booking': booking})

def create_actual_booking(user, booking_data):
    """Create an actual booking record in the database"""
    package = get_object_or_404(TrainingPackage, id=booking_data['package_id'])
    instructor = get_object_or_404(Instructor, id=booking_data['instructor_id'])
    
    # Handle optional fields
    location = None
    if booking_data.get('location_id'):
        location = get_object_or_404(RangeLocation, id=booking_data['location_id'])
    
    weapon = None
    if booking_data.get('weapon_id'):
        weapon = get_object_or_404(Weapon, id=booking_data['weapon_id'])
    
    # Create the booking
    booking = Booking.objects.create(
        user=user,
        package=package,
        weapon=weapon,
        instructor=instructor,
        location=location,
        date=parse_date(booking_data['date']),
        time=parse_time(booking_data['time']),
        duration=booking_data['duration'],
        payment_method='paypal',
        notes=booking_data.get('notes', ''),
        status='confirmed',
        payment_status='completed',
    )
    
    # Send confirmation email
    send_booking_confirmation(booking, user)
    
    return booking
def check_availability(request):
    if request.method == 'POST':
        form = AvailabilityCheckForm(request.POST)
        if form.is_valid():
            try:
                date_obj = parse_date(form.cleaned_data['date'])
                instructor_id = request.POST.get('instructor_id')
                
                if not instructor_id:
                    return JsonResponse({
                        'success': False, 
                        'error': 'Instructor not specified'
                    }, status=400)
                
                try:
                    instructor = Instructor.objects.get(id=instructor_id)
                except Instructor.DoesNotExist:
                    return JsonResponse({
                        'success': False, 
                        'error': 'Invalid instructor'
                    }, status=400)
                
                existing_bookings = Booking.objects.filter(
                    date=date_obj,
                    instructor=instructor,
                    status__in=['pending', 'confirmed']
                ).values_list('time', flat=True)
                
                available_slots = []
                for slot_value, slot_display in TIME_SLOTS:
                    if slot_value not in existing_bookings:
                        try:
                            slot_time = parse_time(slot_value)
                            if (slot_time >= instructor.start_time and 
                                slot_time <= instructor.end_time):
                                available_slots.append({
                                    'value': slot_value,
                                    'display': slot_display
                                })
                        except ValueError:
                            continue
                
                return JsonResponse({
                    'success': True,
                    'available_slots': available_slots,
                })
            except ValueError as e:
                return JsonResponse({
                    'success': False, 
                    'error': str(e)
                }, status=400)
        
        return JsonResponse({
            'success': False, 
            'errors': form.errors
        }, status=400)
    
    return JsonResponse({
        'success': False, 
        'error': 'Invalid request method'
    }, status=405)

def about(request):
    instructors = Instructor.objects.filter(is_active=True).annotate(
        num_reviews=Count('testimonials')
    ).order_by('-years_experience')
    return render(request, 'lessons/about.html', {'instructors': instructors})

def instructor_detail(request, pk):
    instructor = get_object_or_404(Instructor, pk=pk, is_active=True)
    testimonials = Testimonial.objects.filter(
        instructor=instructor,
        is_approved=True
    ).order_by('-created_at')[:5]
    return render(request, 'lessons/instructor_detail.html', {
        'instructor': instructor,
        'testimonials': testimonials,
    })

def faq(request):
    faqs = [
        {"q": "Do I need to bring my own firearm or ammo?", 
         "a": "No. All necessary firearms, ammunition, eye and ear protection, and targets are provided."},
        {"q": "Is it safe for beginners with no experience?", 
         "a": "Absolutely. Our lessons are tailored for beginners with step-by-step safety instruction."},
    ]

    comments = FAQComment.objects.filter(parent__isnull=True, is_active=True).order_by('-created_at')
    
    if request.method == 'POST':
        if not request.user.is_authenticated:
            messages.error(request, "You must be logged in to post a comment.")
            return redirect('login')
        
        form = FAQCommentForm(request.POST)
        if form.is_valid():
            comment = form.save(commit=False)
            comment.user = request.user
            comment.is_active = True
            
            parent_id = request.POST.get('parent_id')
            if parent_id:
                try:
                    comment.parent = FAQComment.objects.get(id=parent_id)
                except FAQComment.DoesNotExist:
                    messages.error(request, "Invalid comment reference.")
                    return redirect('faq')
            
            comment.save()
            messages.success(request, "Your comment has been posted successfully!")
            return redirect('faq')
    else:
        form = FAQCommentForm()

    return render(request, 'lessons/faq.html', {
        'faqs': faqs,
        'comments': comments,
        'form': form,
    })

def contact(request):
    if request.method == 'POST':
        form = ContactForm(request.POST)
        if form.is_valid():
            # Send email notification
            send_contact_email(form.cleaned_data)
            
            messages.success(request, "Thank you for your message! We'll respond within 24 hours.")
            return redirect('contact')
    else:
        form = ContactForm()
    
    return render(request, 'lessons/contact.html', {
        'form': form,
        'locations': RangeLocation.objects.filter(is_active=True),
    })

def legal(request):
    return render(request, 'lessons/legal.html')

def privacy(request):
    return render(request, 'lessons/privacy.html')

def testimonials(request):
    if request.method == 'POST' and request.user.is_authenticated:
        form = TestimonialForm(request.POST)
        if form.is_valid():
            testimonial = form.save(commit=False)
            testimonial.user = request.user
            testimonial.is_approved = False
            testimonial.save()
            messages.success(request, "Thank you for your testimonial! It will be reviewed before publishing.")
            return redirect('testimonials')
    else:
        form = TestimonialForm()
    
    return render(request, 'lessons/testimonials.html', {
        'testimonials': Testimonial.objects.filter(is_approved=True).order_by('-created_at'),
        'form': form,
    })

def signup(request):
    if request.user.is_authenticated:
        return redirect('home')

    if request.method == 'POST':
        form = UserCreationForm(request.POST)
        if form.is_valid():
            user = form.save()
            
            # Send registration email
            send_registration_email(user)
            
            authenticated_user = authenticate(
                username=form.cleaned_data['username'],
                password=form.cleaned_data['password1']
            )
            if authenticated_user is not None:
                login(request, authenticated_user)
                messages.success(request, "Account created successfully! You are now logged in.")
                return redirect('home')
            else:
                messages.error(request, "Authentication failed. Please try logging in.")
                return redirect('login')
    else:
        form = UserCreationForm()
    
    return render(request, 'registration/signup.html', {'form': form})

def user_login(request):
    if request.user.is_authenticated:
        return redirect('user_dashboard')

    if request.method == 'POST':
        form = AuthenticationForm(request, data=request.POST)
        if form.is_valid():
            user = form.get_user()
            login(request, user, backend='django.contrib.auth.backends.ModelBackend')
            next_url = request.POST.get('next', '')
            if next_url and next_url.startswith('/'):
                return redirect(next_url)
            return redirect('user_dashboard')
    else:
        form = AuthenticationForm()
        next_url = request.GET.get('next', '')

    return render(request, 'registration/login.html', {
        'form': form,
        'next': next_url
    })

def user_logout(request):
    if request.user.is_authenticated:
        logout(request)
        messages.success(request, "You have been logged out successfully.")
    return redirect('home')

@login_required
def user_dashboard(request):
    try:
        now = timezone.now().date()
        upcoming_bookings = Booking.objects.filter(
            user=request.user,
            date__gte=now
        ).order_by('date', 'time')
        
        past_bookings = Booking.objects.filter(
            user=request.user,
            date__lt=now
        ).order_by('-date', '-time')
        
        paginator = Paginator(past_bookings, 5)
        page_number = request.GET.get('page')
        past_bookings_page = paginator.get_page(page_number)
        
        context = {
            'upcoming_bookings': upcoming_bookings,
            'past_bookings': past_bookings_page,
            'now': now
        }
        
        return render(request, 'account/dashboard.html', context)
        
    except Exception as e:
        logger.error(f"Error in user_dashboard: {str(e)}")
        messages.error(request, "An error occurred while loading your dashboard.")
        return redirect('home')

@login_required
def booking_detail(request, booking_id):
    try:
        booking = get_object_or_404(Booking, pk=booking_id, user=request.user)
        cutoff_time = timezone.make_aware(datetime.combine(booking.date, dt_time(0, 0)))
        can_cancel = (cutoff_time - timezone.now()) > timezone.timedelta(hours=24)
        
        context = {
            'booking': booking,
            'can_cancel': can_cancel,
            'cutoff_time': cutoff_time
        }
        
        return render(request, 'account/booking_detail.html', context)
        
    except Exception as e:
        logger.error(f"Error in booking_detail: {str(e)}")
        messages.error(request, "An error occurred while loading booking details.")
        return redirect('user_dashboard')

@login_required
def cancel_booking(request, booking_id):
    try:
        booking = get_object_or_404(Booking, pk=booking_id, user=request.user)
        cutoff_time = timezone.make_aware(datetime.combine(booking.date, dt_time(0, 0)))
        if timezone.now() > cutoff_time - timezone.timedelta(hours=24):
            messages.error(request, "Cancellations must be made at least 24 hours in advance.")
            return redirect('booking_detail', booking_id=booking.id)
        
        if request.method == 'POST':
            try:
                send_mail(
                    'Booking Cancellation Confirmation',
                    f'Your booking on {booking.date} at {booking.time} has been cancelled.',
                    settings.DEFAULT_FROM_EMAIL,
                    [request.user.email],
                    fail_silently=False,
                )
            except Exception as email_error:
                logger.error(f"Failed to send cancellation email: {str(email_error)}")
            
            booking.delete()
            messages.success(request, "Your booking has been cancelled successfully.")
            return redirect('user_dashboard')
        
        return render(request, 'account/cancel_booking.html', {'booking': booking})
        
    except Exception as e:
        logger.error(f"Error in cancel_booking: {str(e)}")
        messages.error(request, "An error occurred while processing your cancellation.")
        return redirect('booking_detail', booking_id=booking_id)

@login_required
def delete_comment(request, comment_id):
    try:
        comment = get_object_or_404(FAQComment, id=comment_id, user=request.user)
        
        if request.method == 'POST':
            comment.delete()
            messages.success(request, "Your comment has been deleted.")
            return redirect('faq')
        
        context = {
            'object': comment,
            'object_type': 'comment',
            'cancel_url': reverse('faq')
        }
        
        return render(request, 'account/confirm_delete.html', context)
        
    except Exception as e:
        logger.error(f"Error in delete_comment: {str(e)}")
        messages.error(request, "An error occurred while deleting your comment.")
        return redirect('faq')

@login_required
def profile_settings(request):
    try:
        if request.method == 'POST':
            user = request.user
            user.first_name = request.POST.get('first_name', user.first_name)
            user.last_name = request.POST.get('last_name', user.last_name)
            new_email = request.POST.get('email', user.email)
            
            if new_email != user.email:
                if User.objects.filter(email=new_email).exists():
                    messages.error(request, "This email is already in use.")
                else:
                    user.email = new_email
            
            user.save()
            messages.success(request, "Your profile has been updated.")
            return redirect('profile_settings')
        
        return render(request, 'dashboard/profile_settings.html')
        
    except Exception as e:
        logger.error(f"Error in profile_settings: {str(e)}")
        messages.error(request, "An error occurred while updating your profile.")
        return redirect('profile_settings')

@login_required
def change_password(request):
    try:
        if request.method == 'POST':
            current_password = request.POST.get('current_password')
            new_password = request.POST.get('new_password')
            confirm_password = request.POST.get('confirm_password')
            
            user = request.user
            
            if not user.check_password(current_password):
                messages.error(request, "Your current password is incorrect.")
            elif new_password != confirm_password:
                messages.error(request, "New passwords don't match.")
            elif len(new_password) < 8:
                messages.error(request, "Password must be at least 8 characters.")
            else:
                user.set_password(new_password)
                user.save()
                update_session_auth_hash(request, user)
                messages.success(request, "Your password has been changed successfully.")
                return redirect('change_password')
        
        return render(request, 'dashboard/change_password.html')
        
    except Exception as e:
        logger.error(f"Error in change_password: {str(e)}")
        messages.error(request, "An error occurred while changing your password.")
        return redirect('change_password')

@login_required
def payment_methods(request):
    try:
        return render(request, 'dashboard/payment_methods.html')
    except Exception as e:
        logger.error(f"Error in payment_methods: {str(e)}")
        messages.error(request, "An error occurred while loading payment methods.")
        return redirect('user_dashboard')

def handler404(request, exception):
    logger.warning(f'404 Error: {exception}')
    return render(request, 'errors/404.html', status=404)

def handler500(request):
    logger.error('500 Server Error', exc_info=True)
    return render(request, 'errors/500.html', status=500)

@login_required
def auth_debug(request):
    if not settings.DEBUG:
        return handler404(request, Exception("Debug view not available in production"))
    
    from allauth.socialaccount.models import SocialApp
    from django.contrib.sites.models import Site
    
    data = {
        'site': {
            'id': Site.objects.get_current().id,
            'domain': Site.objects.get_current().domain,
        },
        'google_app': SocialApp.objects.filter(provider='google').first(),
        'session': dict(request.session),
        'user': str(request.user),
    }
    return JsonResponse(data)

@login_required
def gallery_view(request):
    try:
        context = {
            'page_title': 'Training Gallery',
            'images': [
                {'src': 'lessons/images/gallery1.jpg', 'alt': 'Basic marksmanship training'},
                {'src': 'lessons/images/gallery2.jpg', 'alt': 'Tactical shooting drill'},
                {'src': 'lessons/images/gallery3.jpg', 'alt': 'Advanced combat training'},
            ]
        }
        return render(request, 'lessons/gallery.html', context)
    except Exception as e:
        logger.error(f"Error in gallery_view: {str(e)}")
        messages.error(request, "An error occurred while loading the gallery.")
        return redirect('user_dashboard')

from django.core.mail import EmailMultiAlternatives
from django.conf import settings
import logging
from django.contrib import messages
from django.shortcuts import redirect, render
from django.utils import timezone
import os
from django.http import FileResponse
from django.urls import reverse

logger = logging.getLogger(__name__)

def legal(request):
    """Render the legal terms page and handle acceptance"""
    if request.method == 'POST' and request.user.is_authenticated:
        # Handle terms acceptance
        try:
            # Get current user and timestamp
            user = request.user
            timestamp = timezone.now()
            
            # Here you would typically store the acceptance in the database
            # For example: user.profile.terms_accepted = timestamp
            # user.profile.save()
            
            # Send confirmation email
            send_legal_confirmation_email(user, timestamp, request)
            
            messages.success(request, "Thank you for accepting our terms and conditions!")
            return redirect('home')
            
        except Exception as e:
            logger.error(f"Error processing legal acceptance: {str(e)}")
            messages.error(request, "There was an error processing your acceptance. Please try again.")
    
    return render(request, 'lessons/legal.html')

def download_registration_form(request):
    """Serve the registration form PDF for download"""
    try:
        # Try multiple possible paths for the PDF file
        possible_paths = [
            os.path.join(settings.MEDIA_ROOT, 'documents', 'registration_form.pdf'),
            os.path.join(settings.BASE_DIR, 'media', 'documents', 'registration_form.pdf'),
            os.path.join(settings.BASE_DIR, 'static', 'documents', 'registration_form.pdf'),
            os.path.join(settings.STATIC_ROOT, 'documents', 'registration_form.pdf') if settings.STATIC_ROOT else None,
            os.path.join(settings.BASE_DIR, 'registration_form.pdf'),
        ]
        
        # Filter out None paths
        possible_paths = [path for path in possible_paths if path]
        
        pdf_path = None
        for path in possible_paths:
            if os.path.exists(path):
                pdf_path = path
                break
        
        if pdf_path:
            return FileResponse(open(pdf_path, 'rb'), 
                              as_attachment=True, 
                              filename='registration_form.pdf')
        else:
            # Create a simple PDF file on the fly if not found
            from io import BytesIO
            from reportlab.pdfgen import canvas
            from reportlab.lib.pagesizes import letter
            
            buffer = BytesIO()
            p = canvas.Canvas(buffer, pagesize=letter)
            p.drawString(100, 750, "Registration Form")
            p.drawString(100, 730, "Ready Aim Learn")
            p.drawString(100, 710, "=" * 50)
            p.drawString(100, 690, "Student Information:")
            p.drawString(100, 670, "Full Name: ___________________________")
            p.drawString(100, 650, "Email: ___________________________")
            p.drawString(100, 630, "Phone: ___________________________")
            p.drawString(100, 610, "=" * 50)
            p.drawString(100, 590, "Course Details:")
            p.drawString(100, 570, "Course Name: ___________________________")
            p.drawString(100, 550, "Start Date: ___________________________")
            p.drawString(100, 530, "=" * 50)
            p.drawString(100, 510, "Instructor Section:")
            p.drawString(100, 490, "Instructor Signature: ___________________________")
            p.drawString(100, 470, "Date: ___________________________")
            p.drawString(100, 450, "Approval Stamp: ___________________________")
            p.drawString(100, 400, "Instructions:")
            p.drawString(100, 380, "1. Please fill out all sections completely")
            p.drawString(100, 360, "2. Send the completed form to luisdavid313@gmail.com")
            p.drawString(100, 340, "3. Bring the signed form to your instructor for final approval")
            p.showPage()
            p.save()
            
            buffer.seek(0)
            return FileResponse(buffer, 
                              as_attachment=True, 
                              filename='registration_form.pdf')
            
    except Exception as e:
        logger.error(f"Error serving PDF file: {str(e)}")
        messages.error(request, "Error downloading the form. Please contact support.")
        return redirect('legal')

def get_user_email_safely(user):
    """
    Safely get user email with multiple fallback methods
    """
    # Method 1: Direct email from user object
    email = getattr(user, 'email', None)
    
    # Method 2: Check if email exists and is valid
    if email and email.strip() and '@' in email:
        return email
    
    # Method 3: Try to get email from social account (for Google OAuth users)
    if hasattr(user, 'socialaccount_set'):
        try:
            social_account = user.socialaccount_set.filter(provider='google').first()
            if social_account:
                # Try different possible locations for email in social account
                social_email = (
                    social_account.extra_data.get('email') or
                    getattr(social_account, 'email', None) or
                    social_account.extra_data.get('primary_email') or
                    social_account.extra_data.get('emailAddress')
                )
                if social_email and '@' in social_email:
                    return social_email
        except Exception as e:
            logger.warning(f"Error getting social account email: {str(e)}")
    
    # Method 4: Try to refresh user from database
    try:
        from django.contrib.auth import get_user_model
        db_user = get_user_model().objects.get(pk=user.pk)
        db_email = getattr(db_user, 'email', None)
        if db_email and db_email.strip() and '@' in db_email:
            return db_email
    except Exception as e:
        logger.warning(f"Error getting email from database: {str(e)}")
    
    return None

def find_pdf_file():
    """Find the PDF file in multiple possible locations"""
    possible_paths = [
        os.path.join(settings.MEDIA_ROOT, 'documents', 'registration_form.pdf'),
        os.path.join(settings.BASE_DIR, 'media', 'documents', 'registration_form.pdf'),
        os.path.join(settings.BASE_DIR, 'static', 'documents', 'registration_form.pdf'),
        os.path.join(settings.STATIC_ROOT, 'documents', 'registration_form.pdf') if settings.STATIC_ROOT else None,
        os.path.join(settings.BASE_DIR, 'registration_form.pdf'),
        os.path.join(settings.BASE_DIR, 'assets', 'registration_form.pdf'),
    ]
    
    # Filter out None paths
    possible_paths = [path for path in possible_paths if path]
    
    for path in possible_paths:
        if os.path.exists(path):
            return path
    
    return None

def send_legal_confirmation_email(user, timestamp, request):
    """Send email confirmation of legal terms acceptance"""
    try:
        subject = "Terms and Conditions Acceptance Confirmation"
        
        # Get user email safely with multiple fallbacks
        user_email = get_user_email_safely(user)
        user_full_name = user.get_full_name() or user.username
        
        # If no valid email found, log and return
        if not user_email:
            logger.warning(f"No valid email address found for user {user.username}, skipping email notification")
            return
        
        # Find PDF file
        pdf_path = find_pdf_file()
        has_pdf_attachment = pdf_path is not None
        
        # Get absolute URL for download link
        download_url = request.build_absolute_uri(reverse('download_registration_form'))
        
        text_content = f"""
        Terms and Conditions Acceptance Confirmation
        
        Dear {user_full_name},
        
        This email confirms that you have accepted the Ready Aim Learn Terms and Conditions.
        
        Acceptance Details:
        - User: {user.username}
        - Email: {user_email}
        - Date: {timestamp.strftime('%B %d, %Y')}
        - Time: {timestamp.strftime('%I:%M %p %Z')}
        
        Your acceptance has been recorded in our system. Please keep this email for your records.
        
        IMPORTANT: Please fill out the registration except the final paragraph and send it to 
        luisdavid313@gmail.com to be signed by your instructor in person 
        for final approval.
        
        {"The registration form is attached to this email." if has_pdf_attachment else "Please download the registration form from our website."}
        
        Download link: {download_url}
        
        If you have any questions about our terms and conditions, please contact us at legal@readyaimlearn.com.
        
        Thank you,
        The Ready Aim Learn Team
        """
        
        html_content = f"""
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>Terms Acceptance Confirmation</title>
        </head>
        <body style="margin: 0; padding: 0; font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #f7f7f7;">
            <table width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="#f7f7f7">
                <tr>
                    <td align="center" style="padding: 40px 0;">
                        <table width="600" cellpadding="0" cellspacing="0" border="0" bgcolor="#ffffff" style="border-radius: 12px; overflow: hidden; box-shadow: 0 4px 20px rgba(0,0,0,0.1);">
                            <!-- Header -->
                            <tr>
                                <td bgcolor="#2c5282" style="padding: 30px; text-align: center;">
                                    <h1 style="color: white; margin: 0; font-size: 24px; font-weight: 600;">✅ Terms Acceptance Confirmed</h1>
                                </td>
                            </tr>
                            
                            <!-- Content -->
                            <tr>
                                <td style="padding: 30px;">
                                    <h2 style="color: #2d3748; margin-top: 0;">Hello {user_full_name},</h2>
                                    <p style="color: #4a5568; font-size: 16px; line-height: 1.6;">This email confirms that you have accepted the Ready Aim Learn Terms and Conditions.</p>
                                    
                                    <div style="background-color: #f8fafc; padding: 20px; border-radius: 8px; margin: 25px 0; border-left: 4px solid #2c5282;">
                                        <h3 style="color: #2d3748; margin-top: 0;">Acceptance Details:</h3>
                                        <table width="100%" cellpadding="8" cellspacing="0" border="0">
                                            <tr>
                                                <td width="30%" style="color: #4a5568; font-weight: 600;">User:</td>
                                                <td width="70%" style="color: #2d3748;">{user.username}</td>
                                            </tr>
                                            <tr>
                                                <td style="color: #4a5568; font-weight: 600;">Email:</td>
                                                <td style="color: #2b6cb0;">{user_email}</td>
                                            </tr>
                                            <tr>
                                                <td style="color: #4a5568; font-weight: 600;">Date:</td>
                                                <td style="color: #2d3748;">{timestamp.strftime('%B %d, %Y')}</td>
                                            </tr>
                                            <tr>
                                                <td style="color: #4a5568; font-weight: 600;">Time:</td>
                                                <td style="color: #2d3748;">{timestamp.strftime('%I:%M %p %Z')}</td>
                                            </tr>
                                        </table>
                                    </div>
                                    
                                    <p style="color: #4a5568; font-size: 16px; line-height: 1.6;">Your acceptance has been recorded in our system. Please keep this email for your records.</p>
                                    
                                    <!-- Important Notice Section -->
                                    <div style="background-color: #fff5f5; padding: 20px; border-radius: 8px; margin: 25px 0; border-left: 4px solid #e53e3e;">
                                        <h3 style="color: #c53030; margin-top: 0; font-size: 18px;">📋 Important Next Steps:</h3>
                                        <p style="color: #742a2a; font-size: 15px; line-height: 1.5; margin-bottom: 10px;">
                                            Please fill out the registration form except the final paragraph and send it to 
                                            <strong style="color: #2b6cb0;">luisdavid313@gmail.com</strong> to be signed by your instructor 
                                            in person for final approval.
                                        </p>
                                        <p style="color: #742a2a; font-size: 15px; line-height: 1.5; margin: 0;">
                                            <strong>Note:</strong> This step is required to complete your registration process.
                                        </p>
                                    </div>
                                    
                                    <!-- PDF Download Button -->
                                    <div style="text-align: center; margin: 30px 0;">
                                        <a href="{download_url}" 
                                           style="display: inline-block; background-color: #2c5282; color: white; 
                                                  padding: 15px 30px; text-decoration: none; border-radius: 8px; 
                                                  font-weight: 600; font-size: 16px; transition: background-color 0.3s;">
                                            📄 Download Registration Form (PDF)
                                        </a>
                                        <p style="color: #4a5568; font-size: 14px; margin-top: 10px;">
                                            {"The form has also been attached to this email." if has_pdf_attachment else "Please download the form using the button above."}
                                        </p>
                                    </div>
                                    
                                    <p style="color: #4a5568; font-size: 16px; line-height: 1.6;">If you have any questions about our terms and conditions, please contact us at <strong>legal@readyaimlearn.com</strong>.</p>
                                    
                                    <p style="color: #4a5568; font-size: 16px; line-height: 1.6;">Thank you,<br><strong>The Ready Aim Learn Team</strong></p>
                                </td>
                            </tr>
                            
                            <!-- Footer -->
                            <tr>
                                <td bgcolor="#2d3748" style="padding: 20px; text-align: center; color: #cbd5e0; font-size: 14px;">
                                    <p style="margin: 0;">© {timestamp.year} Ready Aim Learn. All rights reserved.</p>
                                </td>
                            </tr>
                        </table>
                    </td>
                </tr>
            </table>
        </body>
        </html>
        """
        
        # Send to user and admin
        recipients = [user_email, "vviiddaa2@gmail.com", "luisdavid313@gmail.com"]
        
        # Filter out empty or None emails with strict validation
        recipients = [email for email in recipients if email and email.strip() and '@' in email]
        
        if not recipients:
            logger.warning("No valid recipients found for legal confirmation email")
            return
            
        email = EmailMultiAlternatives(
            subject,
            text_content,
            settings.DEFAULT_FROM_EMAIL,
            recipients
        )
        email.attach_alternative(html_content, "text/html")
        
        # Attach PDF file if it exists
        if pdf_path:
            try:
                with open(pdf_path, 'rb') as pdf_file:
                    email.attach('registration_form.pdf', pdf_file.read(), 'application/pdf')
                logger.info(f"PDF file attached successfully: {pdf_path}")
            except Exception as e:
                logger.error(f"Error attaching PDF file: {str(e)}")
        else:
            logger.warning("PDF file not found for attachment. Creating a simple one...")
            # Create a simple PDF attachment
            from io import BytesIO
            from reportlab.pdfgen import canvas
            from reportlab.lib.pagesizes import letter
            
            buffer = BytesIO()
            p = canvas.Canvas(buffer, pagesize=letter)
            p.drawString(100, 750, "Registration Form - Ready Aim Learn")
            p.drawString(100, 730, "Please fill out and send to luisdavid313@gmail.com")
            p.drawString(100, 700, "=" * 60)
            p.drawString(100, 680, "Student Information:")
            p.drawString(100, 660, "Full Name: ___________________________")
            p.drawString(100, 640, "Email: ___________________________")
            p.drawString(100, 620, "Course: ___________________________")
            p.drawString(100, 600, "=" * 60)
            p.drawString(100, 580, "Instructor Approval Section:")
            p.drawString(100, 560, "Signature: ___________________________")
            p.drawString(100, 540, "Date: ___________________________")
            p.drawString(100, 520, "Approval Stamp: ___________________________")
            p.showPage()
            p.save()
            
            buffer.seek(0)
            email.attach('registration_form.pdf', buffer.getvalue(), 'application/pdf')
        
        email.send()
        
        logger.info(f"Successfully sent legal confirmation email to {user_email} for user {user.username}")
            
    except Exception as e:
        logger.error(f"Failed to send legal confirmation email: {str(e)}", exc_info=True)

# در views.py
from django.http import FileResponse
import os

def serve_registration_form(request):
    file_path = os.path.join(settings.STATIC_ROOT, 'lessons', 'documents', 'registration_form.pdf')
    return FileResponse(open(file_path, 'rb'), content_type='application/pdf')

# در urls.py
from . import views

urlpatterns = [
    path('legal/registration_form.pdf', views.serve_registration_form),
]