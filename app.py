from flask import Flask, render_template, request, redirect, url_for, session, flash
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, date
import os
import secrets

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'your-secret-key-12345')
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///earnclick.db')
if app.config['SQLALCHEMY_DATABASE_URI'].startswith("postgres://"):
    app.config['SQLALCHEMY_DATABASE_URI'] = app.config['SQLALCHEMY_DATABASE_URI'].replace("postgres://", "postgresql://", 1)

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

# Database Models
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    balance = db.Column(db.Float, default=0.0)
    referral_code = db.Column(db.String(20), unique=True, nullable=False)
    referred_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    last_bonus_date = db.Column(db.Date, nullable=True)
    is_admin = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Ad(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(150), nullable=False)
    url = db.Column(db.String(500), nullable=False)
    reward = db.Column(db.Float, default=0.05)
    duration_seconds = db.Column(db.Integer, default=15)
    active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class AdView(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    ad_id = db.Column(db.Integer, db.ForeignKey('ad.id'), nullable=False)
    view_date = db.Column(db.Date, default=date.today)

class Withdrawal(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    method = db.Column(db.String(50), nullable=False)
    account_number = db.Column(db.String(50), nullable=False)
    status = db.Column(db.String(20), default='Pending')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

# Routes
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    ref_code = request.args.get('ref', '')
    if request.method == 'POST':
        username = request.form.get('username')
        email = request.form.get('email')
        password = request.form.get('password')
        referral_input = request.form.get('referral_code')

        if User.query.filter((User.username == username) | (User.email == email)).first():
            flash('ইউজারনেম অথবা ইমেইল ইতিমধ্যে ব্যবহৃত হয়েছে!', 'danger')
            return redirect(url_for('register'))

        new_ref_code = secrets.token_hex(4).upper()
        hashed_pwd = generate_password_hash(password)
        
        referrer_id = None
        if referral_input:
            referrer = User.query.filter_by(referral_code=referral_input).first()
            if referrer:
                referrer_id = referrer.id
                referrer.balance += 2.00

        new_user = User(
            username=username, 
            email=email, 
            password_hash=hashed_pwd,
            referral_code=new_ref_code,
            referred_by=referrer_id
        )
        
        if User.query.count() == 0:
            new_user.is_admin = True

        db.session.add(new_user)
        db.session.commit()
        flash('নিবন্ধন সফল হয়েছে!', 'success')
        return redirect(url_for('login'))

    return render_template('register.html', ref_code=ref_code)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        user = User.query.filter_by(email=email).first()

        if user and check_password_hash(user.password_hash, password):
            session['user_id'] = user.id
            session['is_admin'] = user.is_admin
            flash('সফলভাবে লগইন করেছেন!', 'success')
            return redirect(url_for('dashboard'))
        
        flash('ভুল ইমেইল অথবা পাসওয়ার্ড!', 'danger')

    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    flash('সফলভাবে লগআউট করা হয়েছে।', 'info')
    return redirect(url_for('login'))

@app.route('/dashboard')
def dashboard():
    user_id = session.get('user_id')
    if not user_id:
        return redirect(url_for('login'))

    user = User.query.get(user_id)
    if not user:
        session.clear()
        return redirect(url_for('login'))

    today = date.today()
    can_claim_bonus = user.last_bonus_date != today

    viewed_ad_ids = [v.ad_id for v in AdView.query.filter_by(user_id=user.id, view_date=today).all()]
    available_ads = Ad.query.filter(Ad.active == True, ~Ad.id.in_(viewed_ad_ids)).all() if viewed_ad_ids else Ad.query.filter_by(active=True).all()
    transactions = Withdrawal.query.filter_by(user_id=user.id).order_by(Withdrawal.created_at.desc()).limit(5).all()
    total_referrals = User.query.filter_by(referred_by=user.id).count()

    return render_template(
        'dashboard.html', 
        user=user, 
        available_ads=available_ads, 
        transactions=transactions,
        can_claim_bonus=can_claim_bonus,
        total_referrals=total_referrals
    )

@app.route('/claim_bonus', methods=['POST'])
def claim_bonus():
    user_id = session.get('user_id')
    if not user_id:
        return redirect(url_for('login'))

    user = User.query.get(user_id)
    today = date.today()

    if user.last_bonus_date != today:
        user.balance += 1.00
        user.last_bonus_date = today
        db.session.commit()
        flash('আজকের ৳১.০০ বোনাস যোগ হয়েছে!', 'success')

    return redirect(url_for('dashboard'))

@app.route('/view_ad/<int:ad_id>', methods=['GET', 'POST'])
def view_ad(ad_id):
    user_id = session.get('user_id')
    if not user_id:
        return redirect(url_for('login'))

    user = User.query.get(user_id)
    ad = Ad.query.get_or_404(ad_id)
    today = date.today()

    already_viewed = AdView.query.filter_by(user_id=user.id, ad_id=ad.id, view_date=today).first()

    if request.method == 'POST':
        if not already_viewed:
            user.balance += ad.reward
            new_view = AdView(user_id=user.id, ad_id=ad.id, view_date=today)
            db.session.add(new_view)
            db.session.commit()
            flash(f'আপনি ৳{ad.reward:.2f} আয় করেছেন!', 'success')
        return redirect(url_for('dashboard'))

    return render_template('view_ad.html', ad=ad, already_viewed=already_viewed)

@app.route('/withdraw', methods=['GET', 'POST'])
def withdraw():
    user_id = session.get('user_id')
    if not user_id:
        return redirect(url_for('login'))

    user = User.query.get(user_id)

    if request.method == 'POST':
        amount = float(request.form.get('amount', 0))
        method = request.form.get('method')
        account_number = request.form.get('account_number')

        if amount < 50:
            flash('সর্বনিম্ন উত্তোলন পরিমাণ ৳৫০.০০!', 'danger')
        elif amount > user.balance:
            flash('পর্যাপ্ত ব্যালেন্স নেই!', 'danger')
        else:
            user.balance -= amount
            new_withdrawal = Withdrawal(
                user_id=user.id,
                amount=amount,
                method=method,
                account_number=account_number
            )
            db.session.add(new_withdrawal)
            db.session.commit()
            flash('উত্তোলন অনুরোধ সফলভাবে জমা হয়েছে!', 'success')
            return redirect(url_for('dashboard'))

    return render_template('withdraw.html', user=user)

# Admin Routes
@app.route('/admin/ads', methods=['GET', 'POST'])
def admin_ads():
    if not session.get('is_admin'):
        flash('আপনার অ্যাডমিন অ্যাক্সেস নেই!', 'danger')
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        title = request.form.get('title')
        url = request.form.get('url')
        reward = float(request.form.get('reward', 0.01))
        duration = int(request.form.get('duration_seconds', 15))

        new_ad = Ad(title=title, url=url, reward=reward, duration_seconds=duration)
        db.session.add(new_ad)
        db.session.commit()
        flash('নতুন বিজ্ঞাপন যোগ করা হয়েছে!', 'success')

    ads = Ad.query.order_by(Ad.created_at.desc()).all()
    return render_template('admin_ads.html', ads=ads)

@app.route('/admin/toggle_ad/<int:ad_id>', methods=['POST'])
def admin_toggle_ad(ad_id):
    if not session.get('is_admin'):
        return redirect(url_for('dashboard'))

    ad = Ad.query.get_or_404(ad_id)
    ad.active = not ad.active
    db.session.commit()
    flash('বিজ্ঞাপনের স্ট্যাটাস পরিবর্তন করা হয়েছে!', 'info')
    return redirect(url_for('admin_ads'))

with app.app_context():
    db.create_all()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
