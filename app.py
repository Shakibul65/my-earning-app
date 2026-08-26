import os
from datetime import datetime, date
from functools import wraps

from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
from flask_sqlalchemy import SQLAlchemy

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'change-this-secret-key')
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///ptc.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    balance = db.Column(db.Float, default=0.0)
    is_admin = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


class Ad(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    url = db.Column(db.String(500), nullable=False)
    reward = db.Column(db.Float, nullable=False, default=0.01)
    duration_seconds = db.Column(db.Integer, nullable=False, default=15)
    active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class AdView(db.Model):
    """Tracks that a user has viewed a specific ad on a specific day (prevents repeat farming)."""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    ad_id = db.Column(db.Integer, db.ForeignKey('ad.id'), nullable=False)
    view_date = db.Column(db.Date, default=date.today)
    reward_given = db.Column(db.Float, default=0.0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint('user_id', 'ad_id', 'view_date', name='uq_user_ad_day'),
    )


class Transaction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    type = db.Column(db.String(20), nullable=False)  # 'earn' | 'withdraw_request' | 'withdraw_approved' | 'withdraw_rejected'
    note = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class WithdrawRequest(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    method = db.Column(db.String(50), nullable=False)  # e.g. bKash, Nagad, Bank
    account_info = db.Column(db.String(255), nullable=False)
    status = db.Column(db.String(20), default='pending')  # pending | approved | rejected
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    processed_at = db.Column(db.DateTime)


# ---------------------------------------------------------------------------
# Helpers / decorators
# ---------------------------------------------------------------------------

def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if 'user_id' not in session:
            flash('আগে লগইন করুন।', 'error')
            return redirect(url_for('login'))
        return view(*args, **kwargs)
    return wrapped


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        user = User.query.get(session['user_id'])
        if not user or not user.is_admin:
            flash('অনুমতি নেই।', 'error')
            return redirect(url_for('dashboard'))
        return view(*args, **kwargs)
    return wrapped


def current_user():
    if 'user_id' in session:
        return User.query.get(session['user_id'])
    return None


@app.context_processor
def inject_helpers():
    return {'get_current_user': current_user}


# ---------------------------------------------------------------------------
# Auth routes
# ---------------------------------------------------------------------------

@app.route('/')
def index():
    if current_user():
        return redirect(url_for('dashboard'))
    return render_template('index.html')


@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '')

        if not username or not email or not password:
            flash('সব ফিল্ড পূরণ করুন।', 'error')
            return redirect(url_for('register'))

        if User.query.filter((User.username == username) | (User.email == email)).first():
            flash('এই ইউজারনেম বা ইমেইল আগে থেকেই ব্যবহৃত হয়েছে।', 'error')
            return redirect(url_for('register'))

        user = User(username=username, email=email)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()

        flash('অ্যাকাউন্ট তৈরি হয়েছে! এখন লগইন করুন।', 'success')
        return redirect(url_for('login'))

    return render_template('register.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        user = User.query.filter_by(username=username).first()

        if user and user.check_password(password):
            session['user_id'] = user.id
            flash(f'স্বাগতম, {user.username}!', 'success')
            return redirect(url_for('dashboard'))

        flash('ভুল ইউজারনেম বা পাসওয়ার্ড।', 'error')
        return redirect(url_for('login'))

    return render_template('login.html')


@app.route('/logout')
def logout():
    session.clear()
    flash('লগআউট সম্পন্ন হয়েছে।', 'success')
    return redirect(url_for('index'))


# ---------------------------------------------------------------------------
# Dashboard & ad-viewing routes
# ---------------------------------------------------------------------------

@app.route('/dashboard')
@login_required
def dashboard():
    user = current_user()
    today = date.today()

    viewed_today_ids = {
        v.ad_id for v in AdView.query.filter_by(user_id=user.id, view_date=today).all()
    }
    ads = Ad.query.filter_by(active=True).all()
    available_ads = [a for a in ads if a.id not in viewed_today_ids]
    completed_today = [a for a in ads if a.id in viewed_today_ids]

    recent_tx = Transaction.query.filter_by(user_id=user.id).order_by(
        Transaction.created_at.desc()
    ).limit(10).all()

    return render_template(
        'dashboard.html',
        user=user,
        available_ads=available_ads,
        completed_today=completed_today,
        recent_tx=recent_tx,
    )


@app.route('/ad/<int:ad_id>')
@login_required
def view_ad(ad_id):
    ad = Ad.query.get_or_404(ad_id)
    today = date.today()

    already = AdView.query.filter_by(user_id=session['user_id'], ad_id=ad.id, view_date=today).first()
    if already:
        flash('আজকে এই বিজ্ঞাপনটি ইতিমধ্যে দেখা হয়ে গেছে।', 'error')
        return redirect(url_for('dashboard'))

    return render_template('view_ad.html', ad=ad)


@app.route('/ad/<int:ad_id>/complete', methods=['POST'])
@login_required
def complete_ad(ad_id):
    """Called via AJAX after the client-side timer finishes."""
    ad = Ad.query.get_or_404(ad_id)
    user = current_user()
    today = date.today()

    already = AdView.query.filter_by(user_id=user.id, ad_id=ad.id, view_date=today).first()
    if already:
        return jsonify({'success': False, 'message': 'ইতিমধ্যে দেখা হয়েছে।'}), 400

    view = AdView(user_id=user.id, ad_id=ad.id, view_date=today, reward_given=ad.reward)
    user.balance += ad.reward
    tx = Transaction(user_id=user.id, amount=ad.reward, type='earn', note=f'বিজ্ঞাপন দেখে আয়: {ad.title}')

    db.session.add(view)
    db.session.add(tx)
    db.session.commit()

    return jsonify({'success': True, 'new_balance': round(user.balance, 4), 'reward': ad.reward})


# ---------------------------------------------------------------------------
# Withdraw routes
# ---------------------------------------------------------------------------

MIN_WITHDRAW = 5.0

@app.route('/withdraw', methods=['GET', 'POST'])
@login_required
def withdraw():
    user = current_user()

    if request.method == 'POST':
        try:
            amount = float(request.form.get('amount', 0))
        except ValueError:
            amount = 0

        method = request.form.get('method', '').strip()
        account_info = request.form.get('account_info', '').strip()

        if amount < MIN_WITHDRAW:
            flash(f'সর্বনিম্ন উত্তোলন পরিমাণ {MIN_WITHDRAW} টাকা/ইউনিট।', 'error')
        elif amount > user.balance:
            flash('পর্যাপ্ত ব্যালেন্স নেই।', 'error')
        elif not method or not account_info:
            flash('পেমেন্ট মেথড ও অ্যাকাউন্ট তথ্য দিন।', 'error')
        else:
            user.balance -= amount
            wr = WithdrawRequest(user_id=user.id, amount=amount, method=method, account_info=account_info)
            tx = Transaction(user_id=user.id, amount=-amount, type='withdraw_request', note=f'{method}-এ উত্তোলন অনুরোধ')
            db.session.add(wr)
            db.session.add(tx)
            db.session.commit()
            flash('উত্তোলনের অনুরোধ জমা হয়েছে। অ্যাডমিন অনুমোদনের অপেক্ষায় আছে।', 'success')
            return redirect(url_for('dashboard'))

    history = WithdrawRequest.query.filter_by(user_id=user.id).order_by(
        WithdrawRequest.created_at.desc()
    ).all()
    return render_template('withdraw.html', user=user, history=history, min_withdraw=MIN_WITHDRAW)


# ---------------------------------------------------------------------------
# Admin routes
# ---------------------------------------------------------------------------

@app.route('/admin')
@admin_required
def admin_dashboard():
    total_users = User.query.count()
    total_ads = Ad.query.count()
    pending_withdrawals = WithdrawRequest.query.filter_by(status='pending').count()
    total_paid_out = db.session.query(db.func.sum(WithdrawRequest.amount)).filter_by(status='approved').scalar() or 0

    return render_template(
        'admin_dashboard.html',
        total_users=total_users,
        total_ads=total_ads,
        pending_withdrawals=pending_withdrawals,
        total_paid_out=total_paid_out,
    )


@app.route('/admin/ads', methods=['GET', 'POST'])
@admin_required
def admin_ads():
    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        url_ = request.form.get('url', '').strip()
        reward = float(request.form.get('reward', 0.01))
        duration = int(request.form.get('duration_seconds', 15))

        if title and url_:
            ad = Ad(title=title, url=url_, reward=reward, duration_seconds=duration)
            db.session.add(ad)
            db.session.commit()
            flash('নতুন বিজ্ঞাপন যোগ হয়েছে।', 'success')
        return redirect(url_for('admin_ads'))

    ads = Ad.query.order_by(Ad.created_at.desc()).all()
    return render_template('admin_ads.html', ads=ads)


@app.route('/admin/ads/<int:ad_id>/toggle', methods=['POST'])
@admin_required
def admin_toggle_ad(ad_id):
    ad = Ad.query.get_or_404(ad_id)
    ad.active = not ad.active
    db.session.commit()
    return redirect(url_for('admin_ads'))


@app.route('/admin/withdrawals')
@admin_required
def admin_withdrawals():
    requests_ = WithdrawRequest.query.order_by(WithdrawRequest.created_at.desc()).all()
    return render_template('admin_withdrawals.html', requests=requests_)


@app.route('/admin/withdrawals/<int:req_id>/<action>', methods=['POST'])
@admin_required
def admin_process_withdrawal(req_id, action):
    wr = WithdrawRequest.query.get_or_404(req_id)

    if wr.status != 'pending':
        flash('এই অনুরোধ ইতিমধ্যে প্রসেস হয়ে গেছে।', 'error')
        return redirect(url_for('admin_withdrawals'))

    if action == 'approve':
        wr.status = 'approved'
        tx = Transaction(user_id=wr.user_id, amount=0, type='withdraw_approved', note=f'উত্তোলন অনুমোদিত: {wr.amount}')
        db.session.add(tx)
        flash('উত্তোলন অনুমোদিত হয়েছে।', 'success')
    elif action == 'reject':
        wr.status = 'rejected'
        user = User.query.get(wr.user_id)
        user.balance += wr.amount  # refund
        tx = Transaction(user_id=wr.user_id, amount=wr.amount, type='withdraw_rejected', note='উত্তোলন প্রত্যাখ্যাত, ব্যালেন্স ফেরত')
        db.session.add(tx)
        flash('উত্তোলন প্রত্যাখ্যান করা হয়েছে ও ব্যালেন্স ফেরত দেওয়া হয়েছে।', 'success')

    wr.processed_at = datetime.utcnow()
    db.session.commit()
    return redirect(url_for('admin_withdrawals'))


# ---------------------------------------------------------------------------
# CLI helper: create initial admin + sample ads
# ---------------------------------------------------------------------------

@app.cli.command('init-db')
def init_db():
    """Usage: flask --app app init-db"""
    db.create_all()

    if not User.query.filter_by(username='admin').first():
        admin = User(username='admin', email='admin@example.com', is_admin=True)
        admin.set_password('admin123')  # CHANGE THIS after first login
        db.session.add(admin)

    if Ad.query.count() == 0:
        sample_ads = [
            Ad(title='Sponsor Ad 1', url='https://example.com/ad1', reward=0.02, duration_seconds=15),
            Ad(title='Sponsor Ad 2', url='https://example.com/ad2', reward=0.03, duration_seconds=20),
            Ad(title='Sponsor Ad 3', url='https://example.com/ad3', reward=0.01, duration_seconds=10),
        ]
        db.session.bulk_save_objects(sample_ads)

    db.session.commit()
    print('Database initialized. Admin login -> username: admin / password: admin123')


if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True, host='0.0.0.0', port=5000)
