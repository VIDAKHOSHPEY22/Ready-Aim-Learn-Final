from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import UserCreationForm, AuthenticationForm
from django.contrib.auth import login, logout, authenticate, update_session_auth_hash
from django.contrib import messages
from django.core.paginator import Paginator
from django.core.mail import EmailMultiAlternatives
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
        <html>
        <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333; max-width: 600px; margin: 0 auto; padding: 20px;">
            <div style="background-color: #d32f2f; color: white; padding: 20px; text-align: center; border-radius: 5px 5px 0 0;">
                <h1 style="margin: 0;">🔫 Shooting Lesson Confirmation</h1>
            </div>
            
            <div style="padding: 20px; background-color: #fff; border-left: 1px solid #eee; border-right: 1px solid #eee;">
                <p>Hello <strong>{user.get_full_name() if user else 'Customer'}</strong>,</p>
                
                <p>Your shooting lesson has been confirmed with these details:</p>
                
                <div style="background-color: #f9f9f9; padding: 15px; border-radius: 5px; margin: 20px 0; border-left: 4px solid #d32f2f;">
                    <h3 style="margin-top: 0;">Lesson Details</h3>
                    <p><strong>Package:</strong> {booking.package.name}</p>
                    <p><strong>Instructor:</strong> {booking.instructor.user.get_full_name()}</p>
                    <p><strong>Date & Time:</strong> {booking.date.strftime('%A, %B %d, %Y')} at {booking.time.strftime('%I:%M %p')}</p>
                    <p><strong>Duration:</strong> {booking.duration} minutes</p>
                    <p><strong>Location:</strong> {booking.location.name if booking.location else 'To be determined'}</p>
                    <p><strong>Total:</strong> ${booking.package.price}</p>
                </div>
                
                <p>Please arrive 15 minutes early for safety briefing.</p>
            </div>
            
            <div style="padding: 20px; text-align: center; font-size: 12px; color: #777; background-color: #f8f9fa; border-radius: 0 0 5px 5px;">
                <p>© {datetime.now().year} Ready Aim Learn. All rights reserved.</p>
            </div>
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

def payment_success(request):
    try:
        latest_ipn = PayPalIPN.objects.filter(
            payment_status="Completed",
            custom=str(request.user.id)
        ).order_by('-created_at').first()
        
        if latest_ipn:
            pending_booking = request.session.get('pending_booking')
            if pending_booking:
                booking = create_actual_booking(request.user, pending_booking)
                del request.session['pending_booking']
                messages.success(request, "Payment successful! Your booking has been confirmed.")
                return redirect('booking_confirmation', booking_id=booking.id)
        
        messages.warning(request, "Your payment was successful but we're processing your booking. You'll receive a confirmation email shortly.")
        return redirect('user_dashboard')
    
    except Exception as e:
        logger.error(f"Error in payment_success: {str(e)}", exc_info=True)
        messages.error(request, "There was an error processing your payment. Please contact support.")
        return redirect('user_dashboard')

def payment_cancel(request):
    messages.warning(request, "Your payment was canceled. You can try again or choose another payment method.")
    return redirect('booking')

@login_required
def booking_confirmation(request, booking_id):
    booking = get_object_or_404(Booking, id=booking_id, user=request.user)
    return render(request, 'booking/confirmation.html', {'booking': booking})

def create_actual_booking(user, booking_data):
    package = get_object_or_404(TrainingPackage, id=booking_data['package_id'])
    instructor = get_object_or_404(Instructor, id=booking_data['instructor_id'])
    location = get_object_or_404(RangeLocation, id=booking_data['location_id']) if booking_data.get('location_id') else None
    weapon = get_object_or_404(Weapon, id=booking_data['weapon_id']) if booking_data.get('weapon_id') else None
    
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
        notes=booking_data['notes'],
        status='confirmed',
        payment_status='completed',
    )
    
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
            send_mail(
                subject=f"New Contact Form Submission from {form.cleaned_data['name']}",
                message=f"Name: {form.cleaned_data['name']}\nEmail: {form.cleaned_data['email']}\n\nMessage:\n{form.cleaned_data['message']}",
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[settings.ADMIN_EMAIL],
                fail_silently=False,
            )
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