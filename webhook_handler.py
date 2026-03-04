import json
import logging
import time
import boto3
from datetime import datetime

logger = logging.getLogger(__name__)

dynamodb           = boto3.resource('dynamodb', region_name='us-east-1')
ses                = boto3.client('ses',      region_name='us-east-1')

APPOINTMENTS_TABLE = "barbershop_appointments"
SHOP_NAME          = "The Barbershop"
SHOP_PHONE         = "+18332895100"
SHOP_EMAIL         = "bookings@yourbarbershop.com"
OWNER_EMAIL        = "owner@yourbarbershop.com"


def parse_booking_signal(text: str) -> dict | None:
    """Extract booking JSON from Claude's BOOK_APPOINTMENT signal."""
    if "BOOK_APPOINTMENT:" not in text:
        return None
    try:
        json_str = text.split("BOOK_APPOINTMENT:")[1].strip()
        json_str = json_str.replace("END_CALL", "").strip()
        start    = json_str.index('{')
        end      = json_str.rindex('}') + 1
        return json.loads(json_str[start:end])
    except Exception as e:
        logger.error("Failed to parse booking signal: %s", e)
        return None


def is_slot_available(date_str: str, time_str: str) -> bool:
    try:
        result = dynamodb.Table(APPOINTMENTS_TABLE).get_item(
            Key={'slot_id': f"{date_str}#{time_str}"}
        )
        return 'Item' not in result
    except Exception as e:
        logger.warning("Availability check error: %s", e)
        return True


def save_appointment(service, date_str, time_str, name, email, caller_phone) -> str:
    appointment_id = f"APT-{int(time.time())}"
    dynamodb.Table(APPOINTMENTS_TABLE).put_item(Item={
        'slot_id':        f"{date_str}#{time_str}",
        'appointment_id': appointment_id,
        'service':        service,
        'date':           date_str,
        'time':           time_str,
        'customer_name':  name,
        'customer_email': email,
        'customer_phone': caller_phone,
        'created_at':     datetime.utcnow().isoformat(),
        'status':         'confirmed',
        'ttl':            int(time.time()) + (90 * 24 * 3600),
        'source':         'voice-ai-engine',
    })
    logger.info("Appointment saved: %s", appointment_id)
    return appointment_id


def send_confirmation_email(name, email, service, date_str, time_str, appointment_id):
    first_name = name.split()[0] if name else "there"
    try:
        ses.send_email(
            Source=SHOP_EMAIL,
            Destination={'ToAddresses': [email]},
            Message={
                'Subject': {'Data': f"Appointment Confirmed at {SHOP_NAME}"},
                'Body': {'Html': {'Data': f"""
                <html><body style="font-family:Arial,sans-serif;padding:20px;max-width:500px">
                <h2>Appointment Confirmed ✂️</h2>
                <p>Hi {first_name},</p>
                <p>Your appointment at <strong>{SHOP_NAME}</strong> is confirmed.</p>
                <table style="border-collapse:collapse;width:100%">
                  <tr style="background:#f5f5f5">
                    <td style="padding:10px;border:1px solid #ddd"><b>Service</b></td>
                    <td style="padding:10px;border:1px solid #ddd">{service.title()}</td>
                  </tr>
                  <tr>
                    <td style="padding:10px;border:1px solid #ddd"><b>Date</b></td>
                    <td style="padding:10px;border:1px solid #ddd">{date_str}</td>
                  </tr>
                  <tr style="background:#f5f5f5">
                    <td style="padding:10px;border:1px solid #ddd"><b>Time</b></td>
                    <td style="padding:10px;border:1px solid #ddd">{time_str}</td>
                  </tr>
                  <tr>
                    <td style="padding:10px;border:1px solid #ddd"><b>Booking ID</b></td>
                    <td style="padding:10px;border:1px solid #ddd">{appointment_id}</td>
                  </tr>
                </table>
                <p>Need to reschedule? Call {SHOP_PHONE}</p>
                </body></html>"""}}
            }
        )
        logger.info("Confirmation email sent to %s", email)
    except Exception as e:
        logger.error("Email error: %s", e)

    # Notify owner
    try:
        ses.send_email(
            Source=SHOP_EMAIL,
            Destination={'ToAddresses': [OWNER_EMAIL]},
            Message={
                'Subject': {'Data': f"New Booking: {name} — {date_str} {time_str}"},
                'Body':    {'Text': {'Data': f"Name: {name}\nPhone: {caller_phone}\nEmail: {email}\nService: {service}\nDate: {date_str}\nTime: {time_str}\nID: {appointment_id}"}}
            }
        )
    except Exception as e:
        logger.error("Owner email error: %s", e)


async def process_booking(booking_data: dict, caller_phone: str) -> str:
    """Process a booking and return the spoken confirmation message."""
    service  = booking_data.get('service', '').lower()
    date_str = booking_data.get('date', '')
    time_str = booking_data.get('time', '')
    name     = booking_data.get('name', '')
    email    = booking_data.get('email', '')

    if not is_slot_available(date_str, time_str):
        return "Oh, it looks like that slot just got taken! Do you have another time in mind?"

    appointment_id = save_appointment(service, date_str, time_str, name, email, caller_phone)

    if email:
        send_confirmation_email(name, email, service, date_str, time_str, appointment_id)

    first_name = name.split()[0] if name else "there"
    return (
        f"You're all set {first_name}! "
        f"We'll see you for your {service} on {date_str} at {time_str}. "
        f"A confirmation email is on its way. Have a great day!"
    )
