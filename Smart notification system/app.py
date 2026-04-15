import os
import uuid
from flask import Flask, render_template, request, redirect, url_for, session, flash
import boto3
from botocore.exceptions import ClientError

app = Flask(__name__)
app.secret_key = 'super_secret_event_key_change_me'

# ==========================================
# HARDCODED ADMIN CREDENTIALS
# ==========================================
ADMIN_USERNAME = 'admin'
ADMIN_PASSWORD = 'password123'

# ==========================================
# AWS CONFIGURATION
# ==========================================
# Change the region to match your AWS setup
AWS_REGION = 'ap-south-1'

# TODO: Replace this with your actual SNS Topic ARN from AWS
SNS_TOPIC_ARN = 'arn:aws:sns:ap-south-1:330905068578:Events' 

# Initialize Boto3 Clients
# Note: Because this runs on EC2 with an IAM Role, we don't need access keys here.
dynamodb = boto3.resource('dynamodb', region_name=AWS_REGION)
sns_client = boto3.client('sns', region_name=AWS_REGION)

# DynamoDB Tables Configuration
# 1. Users Table (Stores registered users)
# 2. Events Table (The "Admin table" where Admin adds events)
# 3. Bookings Table (Stores user ticket purchases)
USERS_TABLE = dynamodb.Table('Users')
EVENTS_TABLE = dynamodb.Table('Events')
BOOKINGS_TABLE = dynamodb.Table('Bookings')


# ==========================================
# HELPER FUNCTIONS
# ==========================================
def send_sns_notification(subject, message):
    """Helper to send SNS notifications"""
    try:
        sns_client.publish(
            TopicArn=SNS_TOPIC_ARN,
            Subject=subject,
            Message=message
        )
    except Exception as e:
        print(f"Error sending SNS: {e}")


# ==========================================
# ROUTES
# ==========================================

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        email = request.form['email']
        
        try:
            # Check if user already exists
            response = USERS_TABLE.get_item(Key={'username': username})
            if 'Item' in response:
                flash('Username already exists. Please choose another.')
                return redirect(url_for('register'))
                
            # Save to DynamoDB
            USERS_TABLE.put_item(
                Item={
                    'username': username,
                    'password': password, # Note: In production, hash passwords!
                    'email': email
                }
            )
            flash('Registration successful! Please login.')
            return redirect(url_for('login'))
        except ClientError as e:
            flash(f"Database error: {e}")
            
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        
        # 1. Check Hardcoded Admin Credentials
        if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
            session['username'] = username
            session['role'] = 'admin'
            return redirect(url_for('admin'))
            
        # 2. Check User Credentials in DynamoDB
        try:
            response = USERS_TABLE.get_item(Key={'username': username})
            if 'Item' in response:
                user = response['Item']
                if user['password'] == password:
                    session['username'] = username
                    session['role'] = 'user'
                    
                    # Trigger SNS Notification on User Login
                    send_sns_notification(
                        subject="User Login Alert",
                        message=f"User '{username}' has just logged into the Smart Event System."
                    )
                    
                    return redirect(url_for('user_dashboard'))
                else:
                    flash('Invalid password!')
            else:
                flash('User not found!')
        except ClientError as e:
            flash(f"Database error: {e}")
            
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

@app.route('/admin', methods=['GET', 'POST'])
def admin():
    if 'username' not in session or session.get('role') != 'admin':
        flash('Unauthorized Access!')
        return redirect(url_for('login'))
        
    if request.method == 'POST':
        event_name = request.form['event_name']
        event_date = request.form['event_date']
        price = request.form['price']
        event_id = str(uuid.uuid4())[:8] # Generate a short unique ID
        
        try:
            EVENTS_TABLE.put_item(
                Item={
                    'event_id': event_id,
                    'event_name': event_name,
                    'event_date': event_date,
                    'price': price
                }
            )
            flash('Event successfully added!')
        except ClientError as e:
            flash(f"Database error: {e}")
            
    # Fetch all events to display to admin
    try:
        response = EVENTS_TABLE.scan()
        events = response.get('Items', [])
    except ClientError:
        events = []
        
    return render_template('admin.html', events=events)

@app.route('/user_dashboard')
def user_dashboard():
    if 'username' not in session or session.get('role') != 'user':
        return redirect(url_for('login'))
        
    # Fetch available events
    try:
        response = EVENTS_TABLE.scan()
        events = response.get('Items', [])
    except ClientError:
        events = []
        
    return render_template('user_dashboard.html', events=events, username=session['username'])

@app.route('/payment/<event_id>')
def payment(event_id):
    if 'username' not in session:
        return redirect(url_for('login'))
        
    try:
        response = EVENTS_TABLE.get_item(Key={'event_id': event_id})
        event = response.get('Item')
        if not event:
            flash('Event not found!')
            return redirect(url_for('user_dashboard'))
            
        return render_template('payment.html', event=event)
    except ClientError as e:
        flash(f"Database error: {e}")
        return redirect(url_for('user_dashboard'))

@app.route('/process_payment', methods=['POST'])
def process_payment():
    if 'username' not in session:
        return redirect(url_for('login'))
        
    event_id = request.form['event_id']
    event_name = request.form['event_name']
    price = request.form['price']
    card_number = request.form['card_number'] # In a real app, use Stripe/PayPal. Don't save this!
    
    booking_id = f"TKT-{str(uuid.uuid4())[:8].upper()}"
    username = session['username']
    
    try:
        # Save Booking to DynamoDB
        BOOKINGS_TABLE.put_item(
            Item={
                'booking_id': booking_id,
                'username': username,
                'event_id': event_id,
                'event_name': event_name,
                'price': price,
                'status': 'PAID'
            }
        )
        
        # Trigger SNS Notification on Event Registration/Booking
        send_sns_notification(
            subject="New Ticket Booking Alert",
            message=f"User '{username}' just booked a ticket for '{event_name}'. Booking ID: {booking_id}. Amount Paid: ${price}."
        )
        
        return redirect(url_for('ticket', booking_id=booking_id))
    except ClientError as e:
        flash(f"Payment Processing Error: {e}")
        return redirect(url_for('user_dashboard'))

@app.route('/ticket/<booking_id>')
def ticket(booking_id):
    if 'username' not in session:
        return redirect(url_for('login'))
        
    try:
        response = BOOKINGS_TABLE.get_item(Key={'booking_id': booking_id})
        booking = response.get('Item')
        
        if not booking or booking['username'] != session['username']:
            flash('Ticket not found or unauthorized access.')
            return redirect(url_for('user_dashboard'))
            
        return render_template('ticket.html', booking=booking)
    except ClientError as e:
        flash(f"Database error: {e}")
        return redirect(url_for('user_dashboard'))

if __name__ == '__main__':
    # Run the app on all interfaces so it's accessible over the internet via EC2 Public IP
    app.run(host='0.0.0.0', port=5000, debug=True)
