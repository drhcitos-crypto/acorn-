from flask import Flask, render_template, jsonify, request, redirect, url_for, session, flash, send_from_directory
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timedelta
from sqlalchemy import text
import hashlib, secrets, os, json
from functools import wraps

app = Flask(__name__)
app.config['SECRET_KEY'] = secrets.token_hex(32)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///acorn.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = os.path.join(os.path.dirname(__file__), 'static', 'uploads')
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
db = SQLAlchemy(app)
app.jinja_env.globals['grade_label'] = lambda v: _grade_label(v)

# ══════════════════════════════════════════════════════════════════
#  MODELS
# ══════════════════════════════════════════════════════════════════

class AdminUser(db.Model):
    id         = db.Column(db.Integer, primary_key=True)
    username   = db.Column(db.String(80), unique=True)
    pw_hash    = db.Column(db.String(128))
    role       = db.Column(db.String(20), default='staff')
    full_name  = db.Column(db.String(120))
    email      = db.Column(db.String(120))
    department = db.Column(db.String(80))
    is_active  = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    notif_off  = db.Column(db.String(500), default='')

    @staticmethod
    def hash_pw(pw): return hashlib.sha256(pw.encode()).hexdigest()
    def check_pw(self, pw): return self.pw_hash == self.hash_pw(pw)

    ROLE_PERMS = {
        'superadmin': {'all'},
        'admin':   {'students','courses','grades','finances','documents','notifications',
                    'exams','admissions','staff','semesters','penalties','messages','tickets','graduation','graduate'},
        'teacher': {'students','courses','grades','documents','notifications','exams','penalties','messages','tickets'},
        'staff':   {'students','documents','notifications','admissions','penalties','messages','tickets','graduate'},
    }
    def can(self, perm):
        p = self.ROLE_PERMS.get(self.role, set())
        return 'all' in p or perm in p
    def role_label(self):
        return {'superadmin':'Super Admin','admin':'Administrator',
                'teacher':'Teacher','staff':'Staff'}.get(self.role, self.role.title())
    def wants_notif(self, key):
        return key not in (self.notif_off or '').split(',')

class Semester(db.Model):
    id         = db.Column(db.Integer, primary_key=True)
    name       = db.Column(db.String(60), unique=True)   # e.g. "Winter 2025"
    start_date = db.Column(db.String(20))
    end_date   = db.Column(db.String(20))
    is_active  = db.Column(db.Boolean, default=False)
    is_current = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Student(db.Model):
    id              = db.Column(db.Integer, primary_key=True)
    student_id      = db.Column(db.String(12), unique=True)
    first_name      = db.Column(db.String(80))
    last_name       = db.Column(db.String(80))
    email           = db.Column(db.String(120))
    pw_hash         = db.Column(db.String(128))
    phone           = db.Column(db.String(40))
    dob             = db.Column(db.String(20))
    address         = db.Column(db.String(200))
    program         = db.Column(db.String(120), default='OSSD — Ontario Secondary School Diploma')
    year_of_study   = db.Column(db.Integer, default=1)
    status          = db.Column(db.String(20), default='active')
    emergency_name  = db.Column(db.String(120))
    emergency_phone = db.Column(db.String(40))
    bio             = db.Column(db.Text)
    extra_credits   = db.Column(db.Float, default=0.0)
    extra_credits_reason = db.Column(db.Text)
    created_at      = db.Column(db.DateTime, default=datetime.utcnow)
    enrollments     = db.relationship('Enrollment', backref='student', lazy=True)
    grades          = db.relationship('Grade', backref='student', lazy=True)
    invoices        = db.relationship('Invoice', backref='student', lazy=True)
    notifications   = db.relationship('Notification', backref='student', lazy=True)

    @staticmethod
    def hash_pw(pw): return hashlib.sha256(pw.encode()).hexdigest()
    def check_pw(self, pw): return self.pw_hash == self.hash_pw(pw)
    def full_name(self): return f"{self.first_name} {self.last_name}"
    def avg_grade(self):
        gs = [g for g in _primary_grade_records(self.grades) if g.percentage is not None]
        if not gs: return 0.0
        return round(sum(g.percentage for g in gs) / len(gs), 1)
    def earned_credits(self):
        VALS = {'0.5':0.5,'1.0':1.0,'2.0':2.0,'0.0':0.0,'NA':0.0}
        gc = sum(VALS.get(str(g.credits), 0) for g in _primary_grade_records(self.grades)
                 if g.percentage is not None and g.percentage >= 50)
        return round(gc + (self.extra_credits or 0), 1)
    def notif_enabled(self, key):
        ns = NotifSetting.query.filter_by(student_id=self.id, notif_key=key).first()
        return ns.enabled if ns else True

class Course(db.Model):
    id          = db.Column(db.Integer, primary_key=True)
    code        = db.Column(db.String(20), unique=True)
    title       = db.Column(db.String(120))
    credits     = db.Column(db.String(5), default='1.0')
    instructor  = db.Column(db.String(80))
    instructor_email = db.Column(db.String(120))
    outline_file = db.Column(db.String(200))
    outline_name = db.Column(db.String(200))
    room        = db.Column(db.String(40))
    prereqs     = db.Column(db.String(200))
    capacity    = db.Column(db.Integer, default=30)
    semester    = db.Column(db.String(60))
    description = db.Column(db.Text)
    department  = db.Column(db.String(60))
    color       = db.Column(db.String(20), default='blue')
    period_start = db.Column(db.String(20))
    period_end   = db.Column(db.String(20))
    weekdays     = db.Column(db.String(30))
    time_start   = db.Column(db.String(5))
    time_end     = db.Column(db.String(5))
    is_active    = db.Column(db.Boolean, default=True)
    is_visible   = db.Column(db.Boolean, default=True)
    is_archived  = db.Column(db.Boolean, default=False)
    course_state = db.Column(db.String(20), default='active')
    enrollments  = db.relationship('Enrollment', backref='course', lazy=True)

    def schedule_display(self):
        parts = []
        if self.weekdays: parts.append(self.weekdays)
        if self.time_start: parts.append(f"{self.time_start}–{self.time_end or ''}")
        return ' · '.join(parts) if parts else '—'
    def enrolled_count(self):
        return Enrollment.query.filter_by(course_id=self.id, status='enrolled').count()
    def waitlist_count(self):
        return Enrollment.query.filter_by(course_id=self.id, status='waitlist').count()
    def availability(self):
        e = self.enrolled_count()
        if e >= self.capacity: return 'waitlist'
        if e >= self.capacity * 0.9: return 'limited'
        return 'open'

class Enrollment(db.Model):
    id           = db.Column(db.Integer, primary_key=True)
    student_id   = db.Column(db.Integer, db.ForeignKey('student.id'))
    course_id    = db.Column(db.Integer, db.ForeignKey('course.id'))
    status       = db.Column(db.String(20), default='enrolled')  # enrolled/waitlist/dropped/withdrawn
    is_approved  = db.Column(db.Boolean, default=False)
    enrolled_at  = db.Column(db.DateTime, default=datetime.utcnow)
    waitlist_pos = db.Column(db.Integer, nullable=True)
    drop_reason  = db.Column(db.Text)   # admin drop/withdrawal reason

class Grade(db.Model):
    id           = db.Column(db.Integer, primary_key=True)
    student_id   = db.Column(db.Integer, db.ForeignKey('student.id'))
    course_code  = db.Column(db.String(20))
    course_title = db.Column(db.String(120))
    semester     = db.Column(db.String(60))
    grade_title  = db.Column(db.String(20), default='overall')
    percentage   = db.Column(db.Float)
    credits      = db.Column(db.String(5), default='1.0')
    status       = db.Column(db.String(20), default='final')

    def grade_color(self):
        if self.percentage is None: return 'var(--g400)'
        if self.percentage >= 80: return 'var(--green)'
        if self.percentage >= 65: return 'var(--blue-mid)'
        if self.percentage >= 50: return 'var(--amber)'
        return 'var(--red)'
    def grade_label(self):
        p = self.percentage
        if p is None: return '—'
        if p >= 90: return 'Outstanding'
        if p >= 80: return 'Excellent'
        if p >= 70: return 'Good'
        if p >= 60: return 'Satisfactory'
        if p >= 50: return 'Passing'
        return 'Failing'

class Invoice(db.Model):
    id              = db.Column(db.Integer, primary_key=True)
    student_id      = db.Column(db.Integer, db.ForeignKey('student.id'))
    description     = db.Column(db.String(200))
    amount          = db.Column(db.Float)
    due_date        = db.Column(db.String(30))
    semester        = db.Column(db.String(60))
    status          = db.Column(db.String(30), default='outstanding')
    # statuses: outstanding / check_pending / paid / rejected
    paid_at         = db.Column(db.DateTime, nullable=True)
    payment_method  = db.Column(db.String(40))
    reject_reason   = db.Column(db.Text)
    receipt_file    = db.Column(db.String(200))
    receipt_name    = db.Column(db.String(200))
    created_at      = db.Column(db.DateTime, default=datetime.utcnow)

class Notification(db.Model):
    id         = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey('student.id'), nullable=True)
    title      = db.Column(db.String(200))
    body       = db.Column(db.Text)
    notif_type = db.Column(db.String(20), default='info')
    is_read    = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Exam(db.Model):
    id           = db.Column(db.Integer, primary_key=True)
    course_id    = db.Column(db.Integer, db.ForeignKey('course.id'), nullable=True)
    course_code  = db.Column(db.String(20))
    course_title = db.Column(db.String(120))
    exam_date    = db.Column(db.String(20))
    start_time   = db.Column(db.String(10))
    end_time     = db.Column(db.String(10))
    room         = db.Column(db.String(40))
    semester     = db.Column(db.String(60), default='Winter 2025')
    notes        = db.Column(db.String(200))
    course       = db.relationship('Course', backref='exams', foreign_keys=[course_id])

class AcademicDocument(db.Model):
    id            = db.Column(db.Integer, primary_key=True)
    student_id    = db.Column(db.Integer, db.ForeignKey('student.id'))
    doc_type      = db.Column(db.String(60))
    requested_at  = db.Column(db.DateTime, default=datetime.utcnow)
    status        = db.Column(db.String(20), default='pending')
    notes         = db.Column(db.String(200))
    reject_reason = db.Column(db.Text)
    filename      = db.Column(db.String(200))
    file_size     = db.Column(db.String(20))

class Todo(db.Model):
    id         = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey('student.id'))
    title      = db.Column(db.String(200))
    due_date   = db.Column(db.String(20))
    course     = db.Column(db.String(30))
    priority   = db.Column(db.String(10), default='medium')
    is_done    = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Note(db.Model):
    id         = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey('student.id'))
    title      = db.Column(db.String(200))
    body       = db.Column(db.Text)
    course     = db.Column(db.String(30))
    color      = db.Column(db.String(20), default='yellow')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class NotifSetting(db.Model):
    id         = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey('student.id'))
    notif_key  = db.Column(db.String(60))
    enabled    = db.Column(db.Boolean, default=True)

class Attendance(db.Model):
    id         = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey('student.id'))
    course_id  = db.Column(db.Integer, db.ForeignKey('course.id'))
    date       = db.Column(db.String(20))
    time       = db.Column(db.String(10))
    type       = db.Column(db.String(10), default='absent')
    duration   = db.Column(db.Float, default=1.0)
    recorded_by= db.Column(db.String(80))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    student    = db.relationship('Student', backref='attendances', foreign_keys=[student_id])
    course     = db.relationship('Course', backref='attendances', foreign_keys=[course_id])

class GraduationWindow(db.Model):
    id              = db.Column(db.Integer, primary_key=True)
    is_open         = db.Column(db.Boolean, default=False)
    graduation_date = db.Column(db.String(20))
    deadline        = db.Column(db.String(20))
    min_credits     = db.Column(db.Float, default=20.0)
    notes           = db.Column(db.Text)
    created_by      = db.Column(db.String(80))
    created_at      = db.Column(db.DateTime, default=datetime.utcnow)

class GraduationApplication(db.Model):
    id              = db.Column(db.Integer, primary_key=True)
    student_id      = db.Column(db.Integer, db.ForeignKey('student.id'))
    window_id       = db.Column(db.Integer, db.ForeignKey('graduation_window.id'))
    status          = db.Column(db.String(20), default='pending')
    submitted_at    = db.Column(db.DateTime, default=datetime.utcnow)
    reviewed_at     = db.Column(db.DateTime, nullable=True)
    reviewed_by     = db.Column(db.String(80))
    admin_note      = db.Column(db.Text)
    credits_at_time = db.Column(db.Float)
    student         = db.relationship('Student', backref='grad_applications')
    window          = db.relationship('GraduationWindow', backref='applications')

class Application(db.Model):
    id              = db.Column(db.Integer, primary_key=True)
    app_number      = db.Column(db.String(20), unique=True)
    submitted_at    = db.Column(db.DateTime, default=datetime.utcnow)
    status          = db.Column(db.String(20), default='pending')
    admin_notes     = db.Column(db.Text, default='')
    reviewed_at     = db.Column(db.DateTime, nullable=True)
    reviewed_by     = db.Column(db.String(80))
    first_name      = db.Column(db.String(80))
    last_name       = db.Column(db.String(80))
    email           = db.Column(db.String(120))
    phone           = db.Column(db.String(40))
    dob             = db.Column(db.String(20))
    gender          = db.Column(db.String(20))
    citizenship     = db.Column(db.String(60))
    first_language  = db.Column(db.String(60))
    applying_grade  = db.Column(db.String(10))
    entry_term      = db.Column(db.String(30))
    current_school  = db.Column(db.String(120))
    current_grade   = db.Column(db.String(10))
    gpa             = db.Column(db.String(10))
    parent_name     = db.Column(db.String(120))
    parent_email    = db.Column(db.String(120))
    parent_phone    = db.Column(db.String(40))
    parent_relation = db.Column(db.String(40))
    address         = db.Column(db.String(200))
    city            = db.Column(db.String(80))
    province        = db.Column(db.String(80))
    postal_code     = db.Column(db.String(20))
    country         = db.Column(db.String(60))
    essay           = db.Column(db.Text)
    activities      = db.Column(db.Text)
    how_heard       = db.Column(db.String(80))
    linked_student_id = db.Column(db.Integer, db.ForeignKey('student.id'), nullable=True)
    def full_name(self): return f"{self.first_name} {self.last_name}"
    def status_label(self):
        return {'pending':'Pending Review','reviewing':'Under Review',
                'approved':'Approved','rejected':'Not Accepted'}.get(self.status, self.status)

class Penalty(db.Model):
    id           = db.Column(db.Integer, primary_key=True)
    student_id   = db.Column(db.Integer, db.ForeignKey('student.id'))
    course_id    = db.Column(db.Integer, db.ForeignKey('course.id'), nullable=True)
    reported_by  = db.Column(db.String(80))   # admin username
    description  = db.Column(db.Text)
    action_type  = db.Column(db.String(30))   # warning/detention/suspension/expulsion
    evidence_file= db.Column(db.String(200))
    evidence_name= db.Column(db.String(200))
    status       = db.Column(db.String(20), default='active')  # active/appealed/resolved/overturned
    created_at   = db.Column(db.DateTime, default=datetime.utcnow)
    student      = db.relationship('Student', backref='penalties', foreign_keys=[student_id])
    course       = db.relationship('Course', backref='penalties', foreign_keys=[course_id])

class PenaltyAppeal(db.Model):
    id           = db.Column(db.Integer, primary_key=True)
    penalty_id   = db.Column(db.Integer, db.ForeignKey('penalty.id'))
    student_id   = db.Column(db.Integer, db.ForeignKey('student.id'))
    reason       = db.Column(db.Text)
    evidence_file= db.Column(db.String(200))
    evidence_name= db.Column(db.String(200))
    submitted_at = db.Column(db.DateTime, default=datetime.utcnow)
    status       = db.Column(db.String(20), default='pending')  # pending/accepted/rejected
    reviewed_by  = db.Column(db.String(80))
    review_note  = db.Column(db.Text)
    penalty      = db.relationship('Penalty', backref='appeals')

class Message(db.Model):
    """Staff internal chat messages."""
    id         = db.Column(db.Integer, primary_key=True)
    sender     = db.Column(db.String(80))   # admin username
    body       = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Consultant(db.Model):
    id              = db.Column(db.Integer, primary_key=True)
    name            = db.Column(db.String(120))
    avatar_url      = db.Column(db.String(300))
    description     = db.Column(db.Text)
    timezone_label  = db.Column(db.String(80), default='Eastern Time (ET)')
    weekday_start   = db.Column(db.String(5), default='09:00')
    weekday_end     = db.Column(db.String(5), default='17:00')
    is_active       = db.Column(db.Boolean, default=True)
    created_at      = db.Column(db.DateTime, default=datetime.utcnow)

class ConsultantMeeting(db.Model):
    id              = db.Column(db.Integer, primary_key=True)
    consultant_id   = db.Column(db.Integer, db.ForeignKey('consultant.id'))
    student_id      = db.Column(db.Integer, db.ForeignKey('student.id'))
    slot_label      = db.Column(db.String(120))
    notes           = db.Column(db.Text)
    status          = db.Column(db.String(20), default='upcoming')
    status_note     = db.Column(db.Text)
    created_at      = db.Column(db.DateTime, default=datetime.utcnow)
    consultant      = db.relationship('Consultant', backref='meetings')
    student         = db.relationship('Student', backref='consultant_meetings')

class ProfileChangeRequest(db.Model):
    id            = db.Column(db.Integer, primary_key=True)
    student_id    = db.Column(db.Integer, db.ForeignKey('student.id'))
    requested_at  = db.Column(db.DateTime, default=datetime.utcnow)
    reviewed_at   = db.Column(db.DateTime, nullable=True)
    reviewed_by   = db.Column(db.String(80))
    status        = db.Column(db.String(20), default='pending')
    payload_json  = db.Column(db.Text)
    admin_note    = db.Column(db.Text)
    student       = db.relationship('Student', backref='profile_change_requests')

class Ticket(db.Model):
    id           = db.Column(db.Integer, primary_key=True)
    student_id   = db.Column(db.Integer, db.ForeignKey('student.id'), nullable=True)
    is_anonymous = db.Column(db.Boolean, default=False)
    title        = db.Column(db.String(200))
    description  = db.Column(db.Text)
    status       = db.Column(db.String(20), default='received')  # received/processing/solved
    reply_text   = db.Column(db.Text)
    reply_file   = db.Column(db.String(200))
    reply_fname  = db.Column(db.String(200))
    replied_by   = db.Column(db.String(80))
    created_at   = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at   = db.Column(db.DateTime, default=datetime.utcnow)

# ══════════════════════════════════════════════════════════════════
#  AUTH HELPERS
# ══════════════════════════════════════════════════════════════════

def student_required(f):
    @wraps(f)
    def d(*a, **kw):
        if not session.get('student_logged_in'): return redirect(url_for('student_login'))
        return f(*a, **kw)
    return d

def admin_required(f):
    @wraps(f)
    def d(*a, **kw):
        if not session.get('admin_logged_in'): return redirect(url_for('admin_login'))
        return f(*a, **kw)
    return d

def perm_required(perm):
    def decorator(f):
        @wraps(f)
        def d(*a, **kw):
            if not session.get('admin_logged_in'): return redirect(url_for('admin_login'))
            u = AdminUser.query.filter_by(username=session.get('admin_user')).first()
            if not u or not u.can(perm):
                flash(f'Access denied. Your role ({u.role_label() if u else "?"}) cannot access this section.', 'error')
                return redirect(url_for('admin_dashboard'))
            return f(*a, **kw)
        return d
    return decorator

def get_student(): return Student.query.get(session.get('student_id')) if session.get('student_id') else None
def get_admin():   return AdminUser.query.filter_by(username=session.get('admin_user')).first()

def _primary_grade_records(grades):
    best_by_course = {}
    for g in grades:
        if (g.grade_title or 'overall').lower() != 'overall':
            continue
        current = best_by_course.get(g.course_code)
        if not current or (g.id or 0) > (current.id or 0):
            best_by_course[g.course_code] = g
    return list(best_by_course.values())

def _normalize_grade_credits(base_credits, percentage=None, enrollment_status=None, grade_title='overall'):
    grade_title = (grade_title or 'overall').lower()
    if grade_title != 'overall':
        return 'NA'
    base = str(base_credits or '0.0')
    if enrollment_status == 'withdrawn':
        return '0.0'
    if percentage is not None and float(percentage) < 50.0:
        return '0.0'
    return base if base in {'0.0', '0.5', '1.0', '2.0', 'NA'} else '1.0'

def _sync_course_grade_credits(student_id, course_code, percentage=None):
    enrollment = Enrollment.query.filter_by(student_id=student_id).join(Course).filter(Course.code == course_code).first()
    enrollment_status = enrollment.status if enrollment else None
    course = enrollment.course if enrollment else Course.query.filter_by(code=course_code).first()
    for g in Grade.query.filter_by(student_id=student_id, course_code=course_code).all():
        pct = g.percentage if g.percentage is not None else percentage
        g.credits = _normalize_grade_credits(course.credits if course else g.credits, pct, enrollment_status, g.grade_title)

def _apply_withdrawal_to_course(student_id, course_id, reason=''):
    enrollment = Enrollment.query.filter_by(student_id=student_id, course_id=course_id).first()
    if enrollment:
        enrollment.status = 'withdrawn'
        enrollment.drop_reason = reason or enrollment.drop_reason or 'Withdrawn'
        enrollment.waitlist_pos = None
    course = Course.query.get(course_id)
    if course:
        for g in Grade.query.filter_by(student_id=student_id, course_code=course.code).all():
            g.credits = '0.0'
    return enrollment, course

def _ensure_attendance_warning(student_id, course_id):
    course = Course.query.get(course_id)
    student = Student.query.get(student_id)
    if not course or not student:
        return
    records = Attendance.query.filter_by(student_id=student_id, course_id=course_id).all()
    absent_hours = round(sum(r.duration or 0 for r in records if r.type == 'absent'), 1)
    late_hours = round(sum(r.duration or 0 for r in records if r.type == 'late'), 1)
    total_hours = round(absent_hours + late_hours, 1)
    if absent_hours < 10 and late_hours < 10 and total_hours < 10:
        return
    description = f'poor attendance on {course.code} {course.title}'
    existing = Penalty.query.filter_by(
        student_id=student_id,
        course_id=course_id,
        action_type='warning',
        description=description
    ).first()
    if existing:
        return
    db.session.add(Penalty(
        student_id=student_id,
        course_id=course_id,
        reported_by='system',
        description=description,
        action_type='warning'
    ))
    _send_notif(student_id, 'Attendance Warning', description, 'warning', 'general')

def _parse_date(value):
    if not value:
        return None
    for fmt in ('%Y-%m-%d', '%Y/%m/%d', '%b %d, %Y'):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None

def _graduation_window_is_open(gw):
    if not gw or not gw.is_open:
        return False
    deadline = _parse_date(gw.deadline)
    if deadline and datetime.utcnow().date() > deadline:
        gw.is_open = False
        db.session.commit()
        return False
    return True

def _grade_label(year_value):
    try:
        return f'Grade {int(year_value) + 8}'
    except (TypeError, ValueError):
        return 'Grade 9'

def _purge_expired_messages():
    today = datetime.utcnow().date()
    cutoff = datetime.combine(today, datetime.min.time())
    stale = Message.query.filter(Message.created_at < cutoff)
    if stale.count():
        stale.delete(synchronize_session=False)
        db.session.commit()

def _profile_request_payload(req):
    try:
        return json.loads(req.payload_json or '{}')
    except json.JSONDecodeError:
        return {}

def _consultant_slots(consultant, days_ahead=10):
    slots = []
    now = datetime.utcnow()
    try:
        start_hour = int((consultant.weekday_start or '09:00').split(':')[0])
        end_hour = int((consultant.weekday_end or '17:00').split(':')[0])
    except ValueError:
        start_hour, end_hour = 9, 17
    for offset in range(days_ahead):
        day = now + timedelta(days=offset)
        if day.weekday() >= 5:
            continue
        for hour in range(start_hour, end_hour):
            slot = f"{day.strftime('%Y-%m-%d')} {hour:02d}:00 {consultant.timezone_label}"
            existing = ConsultantMeeting.query.filter_by(
                consultant_id=consultant.id,
                slot_label=slot,
                status='upcoming'
            ).first()
            if not existing:
                slots.append(slot)
    return slots

def _invoice_due_passed(inv):
    due = _parse_date(inv.due_date)
    if not due:
        return False
    return due < datetime.utcnow().date()

def _refresh_invoice_statuses():
    changed = False
    for inv in Invoice.query.filter(Invoice.status.in_(['outstanding', 'pastdue'])).all():
        should_pastdue = _invoice_due_passed(inv)
        target = 'pastdue' if should_pastdue else 'outstanding'
        if inv.status != target:
            inv.status = target
            changed = True
    if changed:
        db.session.commit()

def _display_before_after(before, after):
    before_val = str(before or 'blank').strip() or 'blank'
    after_val = str(after or 'blank').strip() or 'blank'
    return before_val, after_val

def _profile_change_summary(req):
    payload = _profile_request_payload(req)
    student = req.student
    labels = {
        'first_name': 'First name',
        'last_name': 'Last name',
        'email': 'Email',
        'phone': 'Phone',
        'address': 'Address',
        'emergency_name': 'Emergency contact',
        'emergency_phone': 'Emergency phone',
        'bio': 'Bio',
    }
    changes = []
    for field, label in labels.items():
        if field not in payload:
            continue
        before, after = _display_before_after(getattr(student, field, ''), payload.get(field, ''))
        if before == after:
            continue
        changes.append(f'{label}: {before} -> {after}.')
    if not changes:
        return 'No visible field changes were detected.'
    if len(changes) == 1:
        return changes[0]
    remaining = len(changes) - 1
    if remaining == 1:
        return f'{changes[0]} {changes[1]}'
    return f'{changes[0]} Plus {remaining} more requested changes.'

def _ensure_schema_updates():
    with db.engine.begin() as conn:
        grade_cols = {row[1] for row in conn.execute(text("PRAGMA table_info('grade')")).fetchall()}
        if 'grade_title' not in grade_cols:
            conn.execute(text("ALTER TABLE grade ADD COLUMN grade_title VARCHAR(20) DEFAULT 'overall'"))
        enrollment_cols = {row[1] for row in conn.execute(text("PRAGMA table_info('enrollment')")).fetchall()}
        if 'is_approved' not in enrollment_cols:
            conn.execute(text("ALTER TABLE enrollment ADD COLUMN is_approved BOOLEAN DEFAULT 0"))
        course_cols = {row[1] for row in conn.execute(text("PRAGMA table_info('course')")).fetchall()}
        if 'instructor_email' not in course_cols:
            conn.execute(text("ALTER TABLE course ADD COLUMN instructor_email VARCHAR(120)"))
        if 'outline_file' not in course_cols:
            conn.execute(text("ALTER TABLE course ADD COLUMN outline_file VARCHAR(200)"))
        if 'outline_name' not in course_cols:
            conn.execute(text("ALTER TABLE course ADD COLUMN outline_name VARCHAR(200)"))
        if 'is_visible' not in course_cols:
            conn.execute(text("ALTER TABLE course ADD COLUMN is_visible BOOLEAN DEFAULT 1"))
        if 'is_archived' not in course_cols:
            conn.execute(text("ALTER TABLE course ADD COLUMN is_archived BOOLEAN DEFAULT 0"))
        if 'course_state' not in course_cols:
            conn.execute(text("ALTER TABLE course ADD COLUMN course_state VARCHAR(20) DEFAULT 'active'"))
        invoice_cols = {row[1] for row in conn.execute(text("PRAGMA table_info('invoice')")).fetchall()}
        if 'receipt_file' not in invoice_cols:
            conn.execute(text("ALTER TABLE invoice ADD COLUMN receipt_file VARCHAR(200)"))
        if 'receipt_name' not in invoice_cols:
            conn.execute(text("ALTER TABLE invoice ADD COLUMN receipt_name VARCHAR(200)"))
        if 'status_note' not in {row[1] for row in conn.execute(text("PRAGMA table_info('consultant_meeting')")).fetchall()}:
            conn.execute(text("ALTER TABLE consultant_meeting ADD COLUMN status_note TEXT"))

def _unread(sid):
    return Notification.query.filter(
        (Notification.student_id==sid)|(Notification.student_id==None),
        Notification.is_read==False).count()

def _save_upload(file_field):
    """Save an uploaded file, return (stored_name, original_name) or (None,None)."""
    f = request.files.get(file_field)
    if not f or not f.filename: return None, None
    ext = f.filename.rsplit('.',1)[-1].lower()
    stored = f'{secrets.token_hex(8)}.{ext}'
    f.save(os.path.join(app.config['UPLOAD_FOLDER'], stored))
    return stored, f.filename

def _promote_waitlist(course_id):
    """Promote the first waitlist student when a seat opens."""
    next_wl = Enrollment.query.filter_by(course_id=course_id, status='waitlist')\
                .order_by(Enrollment.waitlist_pos).first()
    if next_wl:
        next_wl.status = 'enrolled'
        next_wl.waitlist_pos = None
        # Renumber remaining waitlist
        remaining = Enrollment.query.filter_by(course_id=course_id, status='waitlist')\
                      .order_by(Enrollment.waitlist_pos).all()
        for i, e in enumerate(remaining, 1): e.waitlist_pos = i
        # Notify student
        db.session.add(Notification(
            student_id=next_wl.student_id,
            title='Waitlist Update — You\'ve been enrolled!',
            body=f'A seat opened up and you have been automatically enrolled in {Course.query.get(course_id).code}. Welcome!',
            notif_type='success'))
        db.session.commit()


def _admin_badge_counts():
    return {
        'adm_pending':   Application.query.filter_by(status='pending').count(),
        'check_pending': Invoice.query.filter_by(status='check_pending').count(),
        'doc_pending':   AcademicDocument.query.filter_by(status='pending').count(),
        'pending_enrolments': Enrollment.query.filter_by(status='enrolled', is_approved=False).count(),
        'profile_change_pending': ProfileChangeRequest.query.filter_by(status='pending').count(),
        'pending_appeals': PenaltyAppeal.query.filter_by(status='pending').count(),
        'open_tickets':  Ticket.query.filter(Ticket.status!='solved').count(),
        'grad_pending':  GraduationApplication.query.filter_by(status='pending').count() if GraduationApplication.query.count() else 0,
    }

def _student_badges(s):
    unread = _unread(s.id)
    active_penalties = Penalty.query.filter_by(student_id=s.id, status='active').count()
    open_tickets = Ticket.query.filter_by(student_id=s.id).filter(Ticket.status!='solved').count()
    gw = GraduationWindow.query.order_by(GraduationWindow.id.desc()).first()
    grad_available = 0
    if _graduation_window_is_open(gw) and s.earned_credits() >= gw.min_credits:
        already = GraduationApplication.query.filter_by(student_id=s.id, window_id=gw.id).first()
        if not already: grad_available = 1
    waitlist_count = Enrollment.query.filter_by(student_id=s.id, status='waitlist').count()
    return {'unread': unread, 'penalties': active_penalties, 'tickets': open_tickets,
            'grad': grad_available, 'waitlist': waitlist_count}

def _send_notif(student_id, title, body, ntype='info', key=None):
    s = Student.query.get(student_id)
    if s and (key is None or s.notif_enabled(key)):
        db.session.add(Notification(student_id=student_id, title=title, body=body, notif_type=ntype))

# ══════════════════════════════════════════════════════════════════
#  STUDENT AUTH
# ══════════════════════════════════════════════════════════════════

@app.route('/login', methods=['GET','POST'])
def student_login():
    if session.get('student_logged_in'): return redirect(url_for('index'))
    if request.method == 'POST':
        s = Student.query.filter_by(student_id=request.form.get('student_id','').strip()).filter(Student.status.in_(['active','graduated'])).first()
        if s and s.check_pw(request.form.get('password','')):
            session['student_logged_in'] = True; session['student_id'] = s.id
            return redirect(url_for('index'))
        flash('Invalid student ID or password.', 'error')
    return render_template('student/login.html')

@app.route('/logout')
def student_logout():
    session.pop('student_logged_in', None); session.pop('student_id', None)
    return redirect(url_for('student_login'))

@app.route('/change-password', methods=['POST'])
@student_required
def change_password():
    s = get_student(); d = request.json
    if not s.check_pw(d.get('old_password','')): return jsonify({'success':False,'message':'Current password incorrect.'})
    new = d.get('new_password','')
    if len(new) < 6: return jsonify({'success':False,'message':'Min 6 characters.'})
    s.pw_hash = Student.hash_pw(new); db.session.commit()
    return jsonify({'success':True,'message':'Password changed.'})

# ══════════════════════════════════════════════════════════════════
#  STUDENT PAGES
# ══════════════════════════════════════════════════════════════════

@app.route('/')
@student_required
def index():
    s = get_student()
    enrolled = Enrollment.query.filter_by(student_id=s.id, status='enrolled', is_approved=True).all()
    notifs   = Notification.query.filter((Notification.student_id==s.id)|(Notification.student_id==None))\
                 .order_by(Notification.created_at.desc()).limit(4).all()
    outstanding = sum(i.amount for i in s.invoices if i.status in ('outstanding','check_pending'))
    enrolled_course_ids = {e.course_id for e in enrolled}
    upcoming_exams = Exam.query.filter(db.or_(Exam.course_id.in_(enrolled_course_ids), Exam.course_code.in_([e.course.code for e in enrolled if e.course])))\
                      .order_by(Exam.exam_date).limit(3).all() if enrolled_course_ids else []
    todos = Todo.query.filter_by(student_id=s.id, is_done=False).order_by(Todo.due_date).limit(5).all()
    active_penalties = [p for p in s.penalties if p.status == 'active']
    s = get_student()
    b = _student_badges(s)
    return render_template('student/dashboard.html', s=s, enrolled=enrolled, notifs=notifs,
        outstanding=outstanding, exams=upcoming_exams, unread=_unread(s.id), b=b,
        todos=todos, active_penalties=active_penalties)

@app.route('/courses')
@student_required
def courses():
    s = get_student()
    all_courses = Course.query.filter_by(is_active=True, is_visible=True, is_archived=False).order_by(Course.code).all()
    enrollments = Enrollment.query.filter_by(student_id=s.id).all()
    enrolled_ids = {e.course_id for e in enrollments if e.status == 'enrolled'}
    approved_ids = {e.course_id for e in enrollments if e.status == 'enrolled' and e.is_approved}
    pending_ids = {e.course_id for e in enrollments if e.status == 'enrolled' and not e.is_approved}
    wishlist_ids = {e.course_id for e in Enrollment.query.filter_by(student_id=s.id, status='wishlist')}
    waitlist_ids = {e.course_id for e in Enrollment.query.filter_by(student_id=s.id, status='waitlist')}
    departments  = [d[0] for d in db.session.query(Course.department).distinct().all() if d[0]]
    waitlist_map = {e.course_id: e.waitlist_pos for e in Enrollment.query.filter_by(student_id=s.id, status='waitlist').all()}
    blocked_course_ids = {p.course_id for p in Penalty.query.filter_by(student_id=s.id, action_type='compulsory_withdrawal').all() if p.course_id}
    grades = Grade.query.filter_by(student_id=s.id).order_by(Grade.id.desc()).all()
    attendance_records = Attendance.query.filter_by(student_id=s.id).order_by(Attendance.date.desc(), Attendance.id.desc()).all()
    grade_map = {}
    for g in grades:
        grade_map.setdefault(g.course_code, {})[(g.grade_title or 'overall').lower()] = g.percentage
    attendance_map = {}
    for record in attendance_records:
        if record.course_id not in attendance_map:
            attendance_map[record.course_id] = []
        attendance_map[record.course_id].append({
            'date': record.date,
            'time': record.time,
            'type': record.type,
            'duration': record.duration,
        })
    b = _student_badges(s)
    return render_template('student/courses.html', s=s, courses=all_courses,
        enrolled_ids=enrolled_ids, approved_ids=approved_ids, pending_ids=pending_ids,
        wishlist_ids=wishlist_ids, waitlist=waitlist_map, blocked_course_ids=blocked_course_ids,
        grade_map=grade_map, attendance_map=attendance_map, departments=departments, b=b, unread=_unread(s.id))

@app.route('/future-consultant')
@student_required
def future_consultant():
    s = get_student()
    consultants = Consultant.query.filter_by(is_active=True).order_by(Consultant.name).all()
    consultant_slots = {c.id: _consultant_slots(c) for c in consultants}
    meetings = ConsultantMeeting.query.filter_by(student_id=s.id).order_by(ConsultantMeeting.created_at.desc()).all()
    b = _student_badges(s)
    return render_template('student/future_consultant.html', s=s, consultants=consultants,
        consultant_slots=consultant_slots, meetings=meetings, b=b, unread=_unread(s.id))

@app.route('/grades')
@student_required
def grades():
    s = get_student()
    history = Grade.query.filter_by(student_id=s.id).order_by(Grade.semester.desc(), Grade.course_code, Grade.id.desc()).all()
    current = Enrollment.query.filter_by(student_id=s.id, status='enrolled', is_approved=True).all()
    b = _student_badges(s)
    return render_template('student/grades.html', s=s, history=history, current=current, b=b, unread=_unread(s.id))

@app.route('/gpa-calculator')
@student_required
def gpa_calc():
    s = get_student()
    history = Grade.query.filter_by(student_id=s.id, status='final').order_by(Grade.semester.desc()).all()
    return render_template('student/gpa_calc.html', s=s, history=history, unread=_unread(s.id))

@app.route('/finances')
@student_required
def finances():
    s = get_student()
    _refresh_invoice_statuses()
    invoices = Invoice.query.filter_by(student_id=s.id).order_by(Invoice.created_at.desc()).all()
    b = _student_badges(s)
    return render_template('student/finances.html', s=s, invoices=invoices,
        outstanding=sum(i.amount for i in invoices if i.status in ('outstanding','pastdue','check_pending')),
        paid_total=sum(i.amount for i in invoices if i.status=='paid'), b=b, unread=_unread(s.id))

@app.route('/schedule')
@student_required
def schedule():
    s = get_student()
    enrolled = Enrollment.query.filter_by(student_id=s.id, status='enrolled', is_approved=True).all()
    courses  = [e.course for e in enrolled]
    enrolled_ids = [e.course_id for e in enrolled]
    enrolled_codes = [c.code for c in courses if c]
    exams = Exam.query.filter(
        db.or_(Exam.course_id.in_(enrolled_ids), Exam.course_code.in_(enrolled_codes))
    ).order_by(Exam.exam_date, Exam.start_time).all() if (enrolled_ids or enrolled_codes) else []
    b=_student_badges(s)
    return render_template('student/schedule.html', s=s, courses=courses, exams=exams, b=b, unread=_unread(s.id))

@app.route('/exams')
@student_required
def student_exams():
    return redirect(url_for('schedule'))

@app.route('/profile')
@student_required
def profile():
    s = get_student()
    b = _student_badges(s)
    pending_request = ProfileChangeRequest.query.filter_by(student_id=s.id, status='pending').order_by(ProfileChangeRequest.requested_at.desc()).first()
    return render_template('student/profile.html', s=s, pending_request=pending_request,
        pending_payload=_profile_request_payload(pending_request) if pending_request else {},
        b=b, unread=_unread(session['student_id']))

@app.route('/documents')
@student_required
def documents():
    s = get_student()
    doc_reqs = AcademicDocument.query.filter_by(student_id=s.id).order_by(AcademicDocument.requested_at.desc()).all()
    b=_student_badges(s)
    doc_options = [
        ('letter_of_acceptance', 'Letter Of Acceptance', False),
        ('graduation_confirmation', 'Graduation Confirmation', False),
        ('ossd', 'Ontario Secondary School Diploma', True),
        ('ossc', 'Ontario Secondary School Certificate', True),
        ('transcript', 'Ontario Student Transcript', True),
    ]
    return render_template('student/documents.html', s=s, doc_reqs=doc_reqs, doc_options=doc_options, b=b, unread=_unread(s.id))

@app.route('/notifications')
@student_required
def notifications():
    s = get_student()
    notifs = Notification.query.filter((Notification.student_id==s.id)|(Notification.student_id==None))\
               .order_by(Notification.created_at.desc()).all()
    b=_student_badges(s)
    return render_template('student/notifications.html', s=s, notifs=notifs, b=b, unread=_unread(s.id))

@app.route('/waitlist')
@student_required
def waitlist():
    s = get_student()
    return render_template('student/waitlist.html', s=s,
        waitlist=Enrollment.query.filter_by(student_id=s.id, status='waitlist').all(), unread=_unread(s.id))

@app.route('/planner')
@student_required
def planner():
    return redirect(url_for('index'))

@app.route('/penalties')
@student_required
def student_penalties():
    s = get_student()
    penalties = Penalty.query.filter_by(student_id=s.id).order_by(Penalty.created_at.desc()).all()
    b=_student_badges(s)
    return render_template('student/penalties.html', s=s, penalties=penalties, b=b, unread=_unread(s.id), now=datetime.utcnow())

@app.route('/tickets')
@student_required
def student_tickets():
    s = get_student()
    tickets = Ticket.query.filter_by(student_id=s.id).order_by(Ticket.created_at.desc()).all()
    b=_student_badges(s)
    return render_template('student/tickets.html', s=s, tickets=tickets, b=b, unread=_unread(s.id))


@app.route('/graduate')
@student_required
def student_graduate():
    s = get_student()
    # Get most recent window (open or closed - student sees status either way)
    gw = GraduationWindow.query.order_by(GraduationWindow.id.desc()).first()
    if gw:
        _graduation_window_is_open(gw)
    existing = GraduationApplication.query.filter_by(student_id=s.id).order_by(GraduationApplication.submitted_at.desc()).first()
    b = _student_badges(s)
    return render_template('student/graduate.html', s=s, gw=gw, existing=existing, b=b, unread=_unread(s.id))

# ══════════════════════════════════════════════════════════════════
#  STUDENT API
# ══════════════════════════════════════════════════════════════════

@app.route('/api/enroll', methods=['POST'])
@student_required
def api_enroll():
    s = get_student(); cid = request.json.get('course_id')
    course = Course.query.get_or_404(cid)
    blocked = Penalty.query.filter_by(student_id=s.id, course_id=cid, action_type='compulsory_withdrawal').first()
    if blocked:
        return jsonify({'success':False,'message':'You are not allowed to re-enrol in this course.'})
    ex = Enrollment.query.filter_by(student_id=s.id, course_id=cid).first()
    if ex:
        if ex.status=='enrolled':
            return jsonify({'success':False,'message':'Enrolment request already submitted.' if not ex.is_approved else 'Already enrolled.'})
        if ex.status=='waitlist':
            return jsonify({'success':False,'message':f'Already on the waitlist for {course.code}. Position #{ex.waitlist_pos}.'})
        if ex.status in ('dropped','withdrawn','wishlist'):
            if course.enrolled_count() >= course.capacity:
                ex.status='waitlist'; ex.waitlist_pos=course.waitlist_count()+1; ex.is_approved=False; db.session.commit()
                return jsonify({'success':True,'waitlist':True,'message':f'Added to waitlist for {course.code}. Position #{ex.waitlist_pos}.'})
            ex.status='enrolled'; ex.waitlist_pos=None; ex.is_approved=False; db.session.commit()
            return jsonify({'success':True,'message':f'Enrolment request sent for {course.code}.'})
    if course.enrolled_count() >= course.capacity:
        pos = course.waitlist_count()+1
        db.session.add(Enrollment(student_id=s.id,course_id=cid,status='waitlist',waitlist_pos=pos,is_approved=False))
        db.session.commit()
        return jsonify({'success':True,'waitlist':True,'message':f'Added to waitlist for {course.code}. Position #{pos}.'})
    db.session.add(Enrollment(student_id=s.id,course_id=cid,status='enrolled',is_approved=False))
    db.session.commit()
    return jsonify({'success':True,'message':f'Enrolment request sent for {course.code}.'})

@app.route('/api/drop', methods=['POST'])
@student_required
def api_drop():
    s = get_student(); cid = request.json.get('course_id')
    # Handle both enrolled and waitlist removal
    e = Enrollment.query.filter_by(student_id=s.id,course_id=cid).filter(
        Enrollment.status.in_(['enrolled','waitlist'])).first()
    if not e: return jsonify({'success':False,'message':'Not enrolled or waitlisted.'})
    was_enrolled = (e.status == 'enrolled' and e.is_approved)
    if e.status == 'waitlist':
        db.session.delete(e)
        db.session.commit()
        return jsonify({'success':True,'message':'Removed from waitlist.'})
    e.status='dropped'; e.is_approved=False; db.session.commit()
    _promote_waitlist(cid)
    return jsonify({'success':True,'message':'Course dropped.'})

@app.route('/api/wishlist/toggle', methods=['POST'])
@student_required
def api_wishlist():
    s = get_student(); cid = request.json.get('course_id')
    e = Enrollment.query.filter_by(student_id=s.id,course_id=cid,status='wishlist').first()
    if e:
        db.session.delete(e); db.session.commit(); return jsonify({'success':True,'added':False})
    db.session.add(Enrollment(student_id=s.id,course_id=cid,status='wishlist'))
    db.session.commit(); return jsonify({'success':True,'added':True})

@app.route('/api/invoice/check-paid', methods=['POST'])
@student_required
def api_invoice_check_paid():
    """Student marks invoice as paid (triggers admin check)."""
    s = get_student(); d = request.json
    inv = Invoice.query.get_or_404(d.get('invoice_id'))
    if inv.student_id != s.id: return jsonify({'success':False,'message':'Unauthorized.'}),403
    if inv.status not in ('outstanding', 'pastdue'): return jsonify({'success':False,'message':'Invoice not in payable status.'})
    inv.status = 'check_pending'
    db.session.commit()
    return jsonify({'success':True,'message':'Payment submission recorded. Pending admin confirmation.'})

@app.route('/api/profile/update', methods=['POST'])
@student_required
def api_profile_update():
    s = get_student(); d = request.json
    allowed = ['first_name','last_name','email','phone','address','emergency_name','emergency_phone','bio']
    payload = {f: d.get(f, '') for f in allowed if f in d}
    if not payload:
        return jsonify({'success':False,'message':'No changes submitted.'}),400
    existing = ProfileChangeRequest.query.filter_by(student_id=s.id, status='pending').first()
    if existing:
        existing.payload_json = json.dumps(payload)
        existing.requested_at = datetime.utcnow()
    else:
        db.session.add(ProfileChangeRequest(student_id=s.id, payload_json=json.dumps(payload)))
    db.session.add(Notification(student_id=s.id,
        title='Profile Update Submitted',
        body='Your profile update request has been sent for admin review.',
        notif_type='info'))
    db.session.commit()
    return jsonify({'success':True,'message':'Profile update submitted for review.'})

@app.route('/api/doc/request', methods=['POST'])
@student_required
def api_doc_request():
    s = get_student()
    d = request.json or {}
    allowed = {'letter_of_acceptance','graduation_confirmation','ossd','ossc','transcript'}
    doc_type = d.get('type','transcript')
    if doc_type not in allowed:
        return jsonify({'success':False,'message':'Unsupported document type.'}),400
    delivery_modes = d.get('delivery_modes', [])
    if not isinstance(delivery_modes, list):
        delivery_modes = []
    notes = d.get('notes','').strip()
    if delivery_modes:
        notes = (notes + '\n' if notes else '') + 'Delivery: ' + ', '.join(delivery_modes)
    db.session.add(AcademicDocument(student_id=s.id, doc_type=doc_type, notes=notes))
    db.session.commit(); return jsonify({'success':True,'message':'Document request submitted.'})

@app.route('/api/consultant/book', methods=['POST'])
@student_required
def api_consultant_book():
    s = get_student()
    d = request.json or {}
    consultant = Consultant.query.get_or_404(d.get('consultant_id'))
    slot_label = d.get('slot_label', '').strip()
    if not slot_label:
        return jsonify({'success':False, 'message':'Please choose a time slot.'}),400
    existing = ConsultantMeeting.query.filter_by(consultant_id=consultant.id, slot_label=slot_label, status='upcoming').first()
    if existing:
        return jsonify({'success':False, 'message':'That time slot has already been booked.'}),400
    db.session.add(ConsultantMeeting(consultant_id=consultant.id, student_id=s.id, slot_label=slot_label, notes=d.get('notes','').strip()))
    _send_notif(s.id, 'Consultant Meeting Booked',
        f'Your meeting with {consultant.name} is booked for {slot_label}.', 'success', 'general')
    db.session.commit()
    return jsonify({'success':True, 'message':'Meeting booked successfully.'})

@app.route('/api/consultant/meeting/<int:mid>/cancel', methods=['POST'])
@student_required
def api_consultant_cancel(mid):
    s = get_student()
    meeting = ConsultantMeeting.query.get_or_404(mid)
    if meeting.student_id != s.id:
        return jsonify({'success':False, 'message':'Unauthorized.'}), 403
    if meeting.status != 'upcoming':
        return jsonify({'success':False, 'message':'Only upcoming meetings can be cancelled.'}), 400
    meeting.status = 'cancelled'
    meeting.status_note = 'Cancelled by student.'
    _send_notif(s.id, 'Consultant Meeting Cancelled',
        f'Your meeting with {meeting.consultant.name} on {meeting.slot_label} has been cancelled.',
        'warning', 'general')
    db.session.commit()
    return jsonify({'success':True, 'message':'Meeting cancelled.'})

@app.route('/api/consultant/meeting/<int:mid>/reschedule', methods=['POST'])
@student_required
def api_consultant_reschedule(mid):
    s = get_student()
    meeting = ConsultantMeeting.query.get_or_404(mid)
    if meeting.student_id != s.id:
        return jsonify({'success':False, 'message':'Unauthorized.'}), 403
    if meeting.status != 'upcoming':
        return jsonify({'success':False, 'message':'Only upcoming meetings can be changed.'}), 400
    d = request.json or {}
    new_slot = d.get('slot_label', '').strip()
    if not new_slot:
        return jsonify({'success':False, 'message':'Please choose a new time.'}), 400
    conflict = ConsultantMeeting.query.filter_by(
        consultant_id=meeting.consultant_id,
        slot_label=new_slot,
        status='upcoming'
    ).filter(ConsultantMeeting.id != meeting.id).first()
    if conflict:
        return jsonify({'success':False, 'message':'That slot is no longer available.'}), 400
    old_slot = meeting.slot_label
    meeting.slot_label = new_slot
    meeting.status_note = f'Rescheduled from {old_slot} by student.'
    _send_notif(s.id, 'Consultant Meeting Updated',
        f'Your meeting with {meeting.consultant.name} has been changed from {old_slot} to {new_slot}.',
        'info', 'general')
    db.session.commit()
    return jsonify({'success':True, 'message':'Meeting date updated.'})

@app.route('/api/doc/upload', methods=['POST'])
@student_required
def api_doc_upload():
    s = get_student()
    if 'file' not in request.files: return jsonify({'success':False,'message':'No file.'})
    f = request.files['file']
    if not f.filename: return jsonify({'success':False,'message':'No file.'})
    ext = f.filename.rsplit('.',1)[-1].lower()
    if ext not in ['pdf','doc','docx','jpg','png']: return jsonify({'success':False,'message':'Type not allowed.'})
    fname = f'{secrets.token_hex(8)}_{f.filename}'
    f.save(os.path.join(app.config['UPLOAD_FOLDER'], fname))
    size = os.path.getsize(os.path.join(app.config['UPLOAD_FOLDER'], fname))
    size_str = f'{size/1024:.1f} KB' if size < 1024*1024 else f'{size/1024/1024:.1f} MB'
    db.session.add(AcademicDocument(student_id=s.id, doc_type=request.form.get('type','uploaded'),
        filename=fname, file_size=size_str, status='ready', notes=f.filename))
    db.session.commit(); return jsonify({'success':True,'message':f'{f.filename} uploaded.'})

@app.route('/api/doc/download/<int:did>')
@student_required
def api_doc_download(did):
    doc = AcademicDocument.query.get_or_404(did)
    if doc.filename:
        return send_from_directory(app.config['UPLOAD_FOLDER'], doc.filename,
            as_attachment=True, download_name=doc.notes or doc.filename)
    return jsonify({'error':'No file'}), 404

@app.route('/api/notif/read/<int:nid>', methods=['POST'])
@student_required
def api_notif_read(nid):
    n = Notification.query.get_or_404(nid); n.is_read=True; db.session.commit()
    return jsonify({'success':True})

@app.route('/api/notif/read-all', methods=['POST'])
@student_required
def api_notif_read_all():
    s = get_student()
    Notification.query.filter((Notification.student_id==s.id)|(Notification.student_id==None),
        Notification.is_read==False).update({'is_read':True})
    db.session.commit(); return jsonify({'success':True})

@app.route('/api/course/outline/<int:cid>')
@student_required
def api_course_outline(cid):
    c = Course.query.get_or_404(cid)
    if not c.outline_file:
        return jsonify({'error':'No outline available.'}),404
    return send_from_directory(app.config['UPLOAD_FOLDER'], c.outline_file,
        as_attachment=True, download_name=c.outline_name or c.outline_file)

@app.route('/api/todo/add', methods=['POST'])
@student_required
def api_todo_add():
    s = get_student(); d = request.json
    t = Todo(student_id=s.id, title=d['title'], due_date=d.get('due_date',''),
        course=d.get('course',''), priority=d.get('priority','medium'))
    db.session.add(t); db.session.commit(); return jsonify({'success':True,'id':t.id})

@app.route('/api/todo/toggle/<int:tid>', methods=['POST'])
@student_required
def api_todo_toggle(tid):
    t = Todo.query.get_or_404(tid); t.is_done=not t.is_done; db.session.commit()
    return jsonify({'success':True,'done':t.is_done})

@app.route('/api/todo/delete/<int:tid>', methods=['POST'])
@student_required
def api_todo_delete(tid):
    t = Todo.query.get_or_404(tid); db.session.delete(t); db.session.commit()
    return jsonify({'success':True})

@app.route('/api/note/add', methods=['POST'])
@student_required
def api_note_add():
    s = get_student(); d = request.json
    db.session.add(Note(student_id=s.id, title=d.get('title','Untitled'),
        body=d.get('body',''), course=d.get('course',''), color=d.get('color','yellow')))
    db.session.commit(); return jsonify({'success':True})

@app.route('/api/note/delete/<int:nid>', methods=['POST'])
@student_required
def api_note_delete(nid):
    n = Note.query.get_or_404(nid); db.session.delete(n); db.session.commit()
    return jsonify({'success':True})

@app.route('/api/penalty/appeal', methods=['POST'])
@student_required
def api_penalty_appeal():
    s = get_student()
    pid = request.form.get('penalty_id')
    penalty = Penalty.query.get_or_404(pid)
    if penalty.student_id != s.id: return jsonify({'success':False,'message':'Unauthorized.'}),403
    if (datetime.utcnow() - penalty.created_at).total_seconds() > 48*3600:
        return jsonify({'success':False,'message':'Appeal window closed (48 hours have passed).'})
    existing = PenaltyAppeal.query.filter_by(penalty_id=pid, student_id=s.id).first()
    if existing: return jsonify({'success':False,'message':'You have already submitted an appeal.'})
    stored, original = _save_upload('evidence')
    appeal = PenaltyAppeal(penalty_id=pid, student_id=s.id,
        reason=request.form.get('reason',''),
        evidence_file=stored, evidence_name=original)
    penalty.status = 'appealed'
    db.session.add(appeal); db.session.commit()
    return jsonify({'success':True,'message':'Appeal submitted. A supervisor will review your case.'})

@app.route('/api/ticket/submit', methods=['POST'])
@student_required
def api_ticket_submit():
    s = get_student(); d = request.json
    anon = d.get('anonymous', False)
    t = Ticket(student_id=None if anon else s.id, is_anonymous=anon,
        title=d.get('title',''), description=d.get('description',''))
    db.session.add(t); db.session.commit()
    return jsonify({'success':True,'id':t.id,'message':'Ticket submitted successfully.'})

# ══════════════════════════════════════════════════════════════════
#  ADMIN AUTH
# ══════════════════════════════════════════════════════════════════

@app.route('/admin/login', methods=['GET','POST'])
def admin_login():
    if request.method == 'POST':
        u = AdminUser.query.filter_by(username=request.form.get('username'), is_active=True).first()
        if u and u.check_pw(request.form.get('password','')):
            session['admin_logged_in'] = True
            session['admin_user']  = u.username
            session['admin_role']  = u.role
            session['admin_name']  = u.full_name or u.username
            return redirect(url_for('admin_dashboard'))
        flash('Invalid username or password.', 'error')
    return render_template('admin/login.html')

@app.route('/admin/logout')
def admin_logout():
    session.clear(); return redirect(url_for('admin_login'))

# ══════════════════════════════════════════════════════════════════
#  ADMIN PAGES
# ══════════════════════════════════════════════════════════════════

@app.route('/admin')
@admin_required
def admin_dashboard():
    user = get_admin()
    stats = {
        'total_students': Student.query.count(),
        'active':         Student.query.filter_by(status='active').count(),
        'total_courses':  Course.query.filter_by(is_active=True).count(),
        'enrollments':    Enrollment.query.filter_by(status='enrolled').count(),
        'outstanding':    db.session.query(db.func.sum(Invoice.amount)).filter(Invoice.status.in_(['outstanding','check_pending'])).scalar() or 0,
        'check_pending':  Invoice.query.filter_by(status='check_pending').count(),
        'doc_pending':    AcademicDocument.query.filter_by(status='pending').count(),
        'adm_pending':    Application.query.filter_by(status='pending').count(),
        'open_tickets':   Ticket.query.filter(Ticket.status!='solved').count(),
        'active_penalties': Penalty.query.filter_by(status='active').count(),
        'pending_appeals':  PenaltyAppeal.query.filter_by(status='pending').count(),
    }
    badges = _admin_badge_counts()
    return render_template('admin/dashboard.html', stats=stats, user=user, badges=badges,
        recent_students=Student.query.order_by(Student.created_at.desc()).limit(8).all(),
        recent_notifs=Notification.query.order_by(Notification.created_at.desc()).limit(5).all(),
        admin=session.get('admin_user'), role=session.get('admin_role'), admin_name=session.get('admin_name'))

@app.route('/admin/students')
@perm_required('students')
def admin_students():
    q=request.args.get('q','').strip(); status=request.args.get('status','all'); prog=request.args.get('program','all')
    query=Student.query
    if q:
        like=f'%{q}%'; query=query.filter(db.or_(Student.first_name.ilike(like),Student.last_name.ilike(like),Student.email.ilike(like),Student.student_id.ilike(like)))
    if status!='all': query=query.filter_by(status=status)
    if prog!='all': query=query.filter_by(program=prog)
    programs=[p[0] for p in db.session.query(Student.program).distinct().all() if p[0]]
    return render_template('admin/students.html', students=query.order_by(Student.last_name).all(),
        q=q, status_filter=status, prog_filter=prog, programs=programs,
        badges=_admin_badge_counts(), admin=session.get('admin_user'), user=get_admin())

@app.route('/admin/student/<int:sid>')
@perm_required('students')
def admin_student_detail(sid):
    s=Student.query.get_or_404(sid)
    badges=_admin_badge_counts()
    return render_template('admin/student_detail.html', s=s, user=get_admin(),
        grades=Grade.query.filter_by(student_id=sid).order_by(Grade.semester.desc()).all(),
        invoices=Invoice.query.filter_by(student_id=sid).order_by(Invoice.created_at.desc()).all(),
        enrolls=Enrollment.query.filter_by(student_id=sid).all(),
        docs=AcademicDocument.query.filter_by(student_id=sid).all(),
        notifs=Notification.query.filter_by(student_id=sid).order_by(Notification.created_at.desc()).all(),
        penalties=Penalty.query.filter_by(student_id=sid).order_by(Penalty.created_at.desc()).all(),
        profile_requests=ProfileChangeRequest.query.filter_by(student_id=sid).order_by(ProfileChangeRequest.requested_at.desc()).all(),
        semesters=Semester.query.order_by(Semester.name).all(),
        active_courses=Course.query.filter_by(is_active=True).order_by(Course.code).all(),
        badges=badges, admin=session.get('admin_user'))

@app.route('/admin/courses')
@perm_required('courses')
def admin_courses():
    archived_count = Course.query.filter_by(is_archived=True).count()
    semesters = Semester.query.order_by(Semester.name).all()
    pending_course_ids = {cid for (cid,) in db.session.query(Enrollment.course_id).filter_by(status='enrolled', is_approved=False).distinct().all()}
    return render_template('admin/courses.html',
        courses=Course.query.filter_by(is_archived=False).order_by(Course.code).all(),
        archived_count=archived_count, semesters=semesters, pending_course_ids=pending_course_ids, badges=_admin_badge_counts(),
        admin=session.get('admin_user'), user=get_admin())

@app.route('/admin/courses/archive')
@perm_required('courses')
def admin_course_archives():
    badges = _admin_badge_counts()
    archived_courses = Course.query.filter_by(is_archived=True).order_by(Course.code).all()
    return render_template('admin/course_archives.html', courses=archived_courses,
        badges=badges, admin=session.get('admin_user'), user=get_admin())

@app.route('/admin/course/<int:cid>/roster')
@perm_required('courses')
def admin_course_roster(cid):
    course = Course.query.get_or_404(cid)
    enrolled = Enrollment.query.filter_by(course_id=cid, status='enrolled').all()
    waitlist = Enrollment.query.filter_by(course_id=cid, status='waitlist').order_by(Enrollment.waitlist_pos).all()
    grades = Grade.query.filter_by(course_code=course.code).all()
    grade_map = {}
    for g in grades:
        current = grade_map.get(g.student_id)
        current_rank = {'overall': 3, 'final': 2, 'midterm': 1}.get((current.grade_title or '').lower(), 0) if current else -1
        new_rank = {'overall': 3, 'final': 2, 'midterm': 1}.get((g.grade_title or '').lower(), 0)
        if not current or (new_rank, g.id) > (current_rank, current.id):
            grade_map[g.student_id] = g
    attendance = Attendance.query.filter_by(course_id=cid).order_by(Attendance.date.desc()).all()
    assigned_ids = {e.student_id for e in Enrollment.query.filter_by(course_id=cid).all()}
    available_students = Student.query.filter(~Student.id.in_(assigned_ids)).order_by(Student.last_name, Student.first_name).all() if assigned_ids else Student.query.order_by(Student.last_name, Student.first_name).all()
    badges = _admin_badge_counts()
    return render_template('admin/course_roster.html', course=course,
        enrolled=enrolled, waitlist=waitlist, grade_map=grade_map,
        attendance=attendance, available_students=available_students, badges=badges,
        admin=session.get('admin_user'), user=get_admin())

@app.route('/admin/finances')
@perm_required('finances')
def admin_finances():
    _refresh_invoice_statuses()
    invoices=Invoice.query.order_by(Invoice.created_at.desc()).all()
    badges=_admin_badge_counts()
    return render_template('admin/finances.html', invoices=invoices,
        students=Student.query.order_by(Student.last_name).all(),
        semesters=Semester.query.order_by(Semester.name).all(),
        total_outstanding=sum(i.amount for i in invoices if i.status in ('outstanding','pastdue','check_pending')),
        total_paid=sum(i.amount for i in invoices if i.status=='paid'),
        check_count=sum(1 for i in invoices if i.status=='check_pending'),
        badges=badges, admin=session.get('admin_user'), user=get_admin())

@app.route('/admin/notifications')
@perm_required('notifications')
def admin_notifications():
    badges=_admin_badge_counts()
    return render_template('admin/notifications.html',
        notifs=Notification.query.order_by(Notification.created_at.desc()).all(),
        students=Student.query.order_by(Student.last_name).all(),
        badges=badges, admin=session.get('admin_user'), user=get_admin())

@app.route('/admin/documents')
@perm_required('documents')
def admin_documents():
    badges=_admin_badge_counts()
    return render_template('admin/documents.html',
        docs=AcademicDocument.query.order_by(AcademicDocument.requested_at.desc()).all(),
        students={s.id:s for s in Student.query.all()},
        badges=badges, admin=session.get('admin_user'), user=get_admin())

@app.route('/admin/exams')
@perm_required('exams')
def admin_exams():
    semesters = Semester.query.order_by(Semester.name).all()
    active_courses = Course.query.filter_by(is_active=True).order_by(Course.code).all()
    badges=_admin_badge_counts()
    return render_template('admin/exams.html',
        exams=Exam.query.order_by(Exam.exam_date).all(),
        semesters=semesters, active_courses=active_courses,
        badges=badges, admin=session.get('admin_user'), user=get_admin())

@app.route('/admin/semesters')
@perm_required('semesters')
def admin_semesters():
    badges=_admin_badge_counts()
    return render_template('admin/semesters.html',
        semesters=Semester.query.order_by(Semester.name.desc()).all(),
        badges=badges, admin=session.get('admin_user'), user=get_admin())

@app.route('/admin/staff')
@perm_required('staff')
def admin_staff():
    badges=_admin_badge_counts()
    return render_template('admin/staff.html',
        staff=AdminUser.query.order_by(AdminUser.role, AdminUser.username).all(),
        badges=badges, admin=session.get('admin_user'), user=get_admin())

@app.route('/admin/penalties')
@perm_required('penalties')
def admin_penalties():
    appeals = PenaltyAppeal.query.filter_by(status='pending').all()
    badges=_admin_badge_counts()
    return render_template('admin/penalties.html',
        penalties=Penalty.query.order_by(Penalty.created_at.desc()).all(),
        appeals=appeals,
        students=Student.query.order_by(Student.last_name).all(),
        active_courses=Course.query.filter_by(is_active=True).order_by(Course.code).all(),
        badges=badges, admin=session.get('admin_user'), user=get_admin())

@app.route('/admin/messages')
@perm_required('messages')
def admin_messages():
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/future-consultant')
@perm_required('students')
def admin_future_consultant():
    consultants = Consultant.query.order_by(Consultant.name).all()
    upcoming = ConsultantMeeting.query.order_by(ConsultantMeeting.created_at.desc()).all()
    consultant_slots = {c.id: _consultant_slots(c) for c in consultants}
    badges = _admin_badge_counts()
    return render_template('admin/future_consultant.html', consultants=consultants, upcoming=upcoming, consultant_slots=consultant_slots,
        badges=badges, admin=session.get('admin_user'), user=get_admin())

@app.route('/admin/profile-requests')
@perm_required('students')
def admin_profile_requests():
    reqs = ProfileChangeRequest.query.order_by(ProfileChangeRequest.requested_at.desc()).all()
    badges = _admin_badge_counts()
    summaries = {req.id: _profile_change_summary(req) for req in reqs}
    return render_template('admin/profile_requests.html', reqs=reqs, summaries=summaries,
        badges=badges, admin=session.get('admin_user'), user=get_admin())

@app.route('/admin/tickets')
@perm_required('tickets')
def admin_tickets():
    status_f = request.args.get('status','all')
    q = Ticket.query
    if status_f != 'all': q = q.filter_by(status=status_f)
    students = {s.id:s for s in Student.query.all()}
    badges=_admin_badge_counts()
    return render_template('admin/tickets.html',
        tickets=q.order_by(Ticket.created_at.desc()).all(),
        students=students, status_f=status_f,
        badges=badges, admin=session.get('admin_user'), user=get_admin())

@app.route('/admin/admissions')
@perm_required('admissions')
def admin_admissions():
    sf=request.args.get('status','all'); gf=request.args.get('grade','all'); tf=request.args.get('term','all'); q=request.args.get('q','').strip()
    query=Application.query
    if sf!='all': query=query.filter_by(status=sf)
    if gf!='all': query=query.filter_by(applying_grade=gf)
    if tf!='all': query=query.filter_by(entry_term=tf)
    if q:
        like=f'%{q}%'; query=query.filter(db.or_(Application.first_name.ilike(like),Application.last_name.ilike(like),Application.email.ilike(like),Application.app_number.ilike(like)))
    stats={'total':Application.query.count(),'pending':Application.query.filter_by(status='pending').count(),
        'reviewing':Application.query.filter_by(status='reviewing').count(),
        'approved':Application.query.filter_by(status='approved').count(),
        'rejected':Application.query.filter_by(status='rejected').count()}
    badges=_admin_badge_counts()
    return render_template('admin/admissions.html', apps=query.order_by(Application.submitted_at.desc()).all(),
        stats=stats, status_f=sf, grade_f=gf, term_f=tf, q=q,
        grades=sorted([g[0] for g in db.session.query(Application.applying_grade).distinct().all() if g[0]]),
        terms=sorted([t[0] for t in db.session.query(Application.entry_term).distinct().all() if t[0]]),
        semesters=Semester.query.order_by(Semester.name).all(),
        badges=badges, admin=session.get('admin_user'), user=get_admin())

@app.route('/admin/admissions/<int:app_id>')
@perm_required('admissions')
def admin_admissions_detail(app_id):
    app_obj=Application.query.get_or_404(app_id)
    badges=_admin_badge_counts()
    return render_template('admin/admissions_detail.html', app=app_obj,
        linked=Student.query.get(app_obj.linked_student_id) if app_obj.linked_student_id else None,
        badges=badges, admin=session.get('admin_user'), user=get_admin())

# ══════════════════════════════════════════════════════════════════
#  ADMIN API
# ══════════════════════════════════════════════════════════════════

@app.route('/api/admin/student/add', methods=['POST'])
@perm_required('students')
def api_student_add():
    d=request.json; sid=f"CNA{secrets.randbelow(900000)+100000}"
    db.session.add(Student(student_id=sid,first_name=d.get('first_name',''),last_name=d.get('last_name',''),
        email=d.get('email',''),program=d.get('program',''),year_of_study=int(d.get('year',1)),
        phone=d.get('phone',''),pw_hash=Student.hash_pw(d.get('password','123456'))))
    db.session.commit(); s=Student.query.filter_by(student_id=sid).first()
    return jsonify({'success':True,'id':s.id,'student_id':sid})

@app.route('/api/admin/student/update/<int:sid>', methods=['POST'])
@perm_required('students')
def api_student_update(sid):
    s=Student.query.get_or_404(sid); d=request.json
    for f in ['first_name','last_name','email','phone','program','year_of_study','status','address','emergency_name','emergency_phone']:
        if f in d: setattr(s,f,d[f])
    if d.get('reset_password'): s.pw_hash=Student.hash_pw('123456')
    db.session.commit(); return jsonify({'success':True})

@app.route('/api/admin/student/grades/<int:sid>')
@perm_required('grades')
def api_admin_student_grades(sid):
    s = Student.query.get_or_404(sid)
    grades = Grade.query.filter_by(student_id=sid).order_by(Grade.semester.desc(), Grade.course_code, Grade.id.desc()).all()
    return jsonify({
        'student_name': s.full_name(),
        'grades': [{
            'id': g.id,
            'code': g.course_code,
            'title': g.course_title,
            'semester': g.semester,
            'grade_title': (g.grade_title or 'overall').title(),
            'pct': g.percentage,
            'credits': g.credits,
            'status': g.status
        } for g in grades]
    })

@app.route('/api/admin/grade/add', methods=['POST'])
@perm_required('grades')
def api_grade_add():
    d=request.json or {}
    if not d.get('student_id') or not d.get('code'):
        return jsonify({'success':False,'error':'Student and course code are required.'}),400
    if d.get('percentage') in (None, ''):
        return jsonify({'success':False,'error':'Grade percentage is required.'}),400
    percentage = float(d['percentage']) if d.get('percentage') not in (None, '') else None
    semester = d.get('semester')
    if not semester and d.get('semester_id'):
        sem = Semester.query.get(d.get('semester_id'))
        semester = sem.name if sem else ''
    grade_title = (d.get('grade_title') or 'overall').lower()
    course = Course.query.filter_by(code=d.get('code', '')).first()
    enrollment = Enrollment.query.filter_by(student_id=d['student_id'], course_id=course.id if course else None).first() if course else None
    credits = _normalize_grade_credits(
        d.get('credits') or (course.credits if course else '1.0'),
        percentage,
        enrollment.status if enrollment else None,
        grade_title
    )
    existing = Grade.query.filter_by(student_id=d['student_id'], course_code=d['code'], grade_title=grade_title).first()
    if existing:
        existing.course_title = d.get('title', existing.course_title)
        existing.semester = semester or existing.semester
        existing.percentage = percentage
        existing.credits = credits
        existing.status = grade_title
    else:
        db.session.add(Grade(student_id=d['student_id'],course_code=d['code'],
            course_title=d.get('title',''),semester=semester or '',
            grade_title=grade_title, percentage=percentage,
            credits=credits, status=grade_title))
    _sync_course_grade_credits(d['student_id'], d['code'], percentage)
    s = Student.query.get(d['student_id'])
    if s:
        grade_title_label = _grade_label(grade_title)
        _send_notif(s.id, f'Grade Updated: {d["code"]} {grade_title_label}',
            f'Your {grade_title_label.lower()} grade for {d["code"]} has been updated to {d["percentage"]}%.',
            'info', 'grade_added')
    db.session.commit(); return jsonify({'success':True})

@app.route('/api/admin/grade/delete/<int:gid>', methods=['POST'])
@perm_required('grades')
def api_grade_delete(gid):
    g=Grade.query.get_or_404(gid); db.session.delete(g); db.session.commit()
    return jsonify({'success':True})

@app.route('/api/admin/invoice/add', methods=['POST'])
@perm_required('finances')
def api_invoice_add():
    d=request.json or {}
    student_id = d.get('student_id')
    description = (d.get('description') or '').strip()
    amount = d.get('amount')
    if not student_id:
        return jsonify({'success':False,'error':'Student is required.'}),400
    if not description:
        return jsonify({'success':False,'error':'Description is required.'}),400
    if amount in (None, ''):
        return jsonify({'success':False,'error':'Amount is required.'}),400
    try:
        amount = float(amount)
    except (TypeError, ValueError):
        return jsonify({'success':False,'error':'Amount must be a valid number.'}),400
    if amount < 0:
        return jsonify({'success':False,'error':'Amount cannot be negative.'}),400
    db.session.add(Invoice(student_id=student_id, description=description,
        amount=amount, due_date=d.get('due_date',''),
        semester=d.get('semester',''), status=d.get('status','outstanding')))
    db.session.flush()
    _refresh_invoice_statuses()
    db.session.commit(); return jsonify({'success':True})

@app.route('/api/admin/invoice/confirm/<int:iid>', methods=['POST'])
@perm_required('finances')
def api_invoice_confirm(iid):
    inv=Invoice.query.get_or_404(iid)
    if inv.status not in ('outstanding', 'pastdue', 'check_pending'):
        return jsonify({'success':False,'error':'Invoice cannot be confirmed from its current status.'}),400
    f = request.files.get('receipt')
    if not f or not f.filename:
        return jsonify({'success':False,'error':'Receipt file is required.'}),400
    ext = f.filename.rsplit('.', 1)[-1].lower()
    if ext not in ['pdf', 'png', 'jpg', 'jpeg']:
        return jsonify({'success':False,'error':'Receipt file type not allowed.'}),400
    stored = f'{secrets.token_hex(8)}.{ext}'
    f.save(os.path.join(app.config['UPLOAD_FOLDER'], stored))
    inv.status='paid'; inv.paid_at=datetime.utcnow(); inv.payment_method='Manual Transfer'
    inv.receipt_file = stored
    inv.receipt_name = f.filename
    s=Student.query.get(inv.student_id)
    if s:
        db.session.add(Notification(student_id=s.id,
            title='Payment Confirmed',
            body=f'Your payment of ${inv.amount:.2f} for "{inv.description}" has been confirmed by the finance office. Your receipt is now available for download.',
            notif_type='success'))
    db.session.commit(); return jsonify({'success':True})

@app.route('/api/admin/invoice/reject-check/<int:iid>', methods=['POST'])
@perm_required('finances')
def api_invoice_reject_check(iid):
    inv=Invoice.query.get_or_404(iid); d=request.json
    inv.status='pastdue' if _invoice_due_passed(inv) else 'outstanding'
    inv.reject_reason=d.get('reason','')
    s=Student.query.get(inv.student_id)
    if s:
        db.session.add(Notification(student_id=s.id,
            title='Payment Could Not Be Verified',
            body=f'Your payment submission for "{inv.description}" could not be verified.\n\nReason: {inv.reject_reason}\n\nPlease contact the finance office.',
            notif_type='warning'))
    db.session.commit(); return jsonify({'success':True})

@app.route('/api/admin/invoice/update/<int:iid>', methods=['POST'])
@perm_required('finances')
def api_invoice_update(iid):
    inv=Invoice.query.get_or_404(iid); d=request.json
    previous_status = inv.status
    inv.status=d.get('status',inv.status)
    if inv.status=='paid' and not inv.paid_at: inv.paid_at=datetime.utcnow()
    if previous_status == 'paid' and inv.status in ('outstanding', 'pastdue'):
        reason = d.get('reason','').strip()
        if not reason:
            return jsonify({'success':False,'error':'Reason required when reopening a paid invoice.'}),400
        inv.paid_at = None
        s=Student.query.get(inv.student_id)
        if s:
            db.session.add(Notification(student_id=s.id,
                title='Invoice Reopened',
                body=f'Your invoice "{inv.description}" has been changed back to outstanding.\n\nReason: {reason}',
                notif_type='warning'))
        inv.receipt_file = None
        inv.receipt_name = None
        if inv.status == 'outstanding' and _invoice_due_passed(inv):
            inv.status = 'pastdue'
    db.session.commit(); return jsonify({'success':True})

@app.route('/api/admin/course/add', methods=['POST'])
@perm_required('courses')
def api_course_add():
    d=request.json
    c = Course(code=d['code'],title=d['title'],credits=d.get('credits','1.0'),
        instructor=d.get('instructor',''),instructor_email=d.get('instructor_email',''),
        room=d.get('room',''),prereqs=d.get('prereqs',''),
        capacity=int(d.get('capacity',30)),semester=d.get('semester',''),
        department=d.get('department',''),color=d.get('color','blue'),
        description=d.get('description',''),
        period_start=d.get('period_start',''),period_end=d.get('period_end',''),
        weekdays=d.get('weekdays',''),time_start=d.get('time_start',''),time_end=d.get('time_end',''))
    db.session.add(c)
    db.session.commit(); return jsonify({'success':True, 'id': c.id})

@app.route('/api/admin/course/update/<int:cid>', methods=['POST'])
@perm_required('courses')
def api_course_update(cid):
    c=Course.query.get_or_404(cid); d=request.json
    for f in ['title','instructor','room','prereqs','capacity','semester','department','is_active',
              'credits','color','description','period_start','period_end','weekdays','time_start','time_end',
              'instructor_email']:
        if f in d: setattr(c,f,d[f])
    db.session.commit(); return jsonify({'success':True})

@app.route('/api/admin/course/outline/<int:cid>', methods=['POST'])
@perm_required('courses')
def api_course_outline_upload(cid):
    c = Course.query.get_or_404(cid)
    f = request.files.get('outline')
    if not f or not f.filename:
        return jsonify({'success':False,'error':'No outline selected.'}),400
    ext = f.filename.rsplit('.', 1)[-1].lower()
    if ext not in ['pdf', 'doc', 'docx']:
        return jsonify({'success':False,'error':'Outline file type not allowed.'}),400
    stored = f'{secrets.token_hex(8)}.{ext}'
    f.save(os.path.join(app.config['UPLOAD_FOLDER'], stored))
    c.outline_file = stored
    c.outline_name = f.filename
    db.session.commit()
    return jsonify({'success':True})

@app.route('/api/admin/consultant/save', methods=['POST'])
@perm_required('students')
def api_consultant_save():
    d = request.json or {}
    cid = d.get('id')
    consultant = Consultant.query.get(cid) if cid else Consultant()
    if not cid:
        db.session.add(consultant)
    for field in ['name', 'avatar_url', 'description', 'timezone_label', 'weekday_start', 'weekday_end', 'is_active']:
        if field in d:
            setattr(consultant, field, d[field])
    db.session.commit()
    return jsonify({'success':True, 'id': consultant.id})

@app.route('/api/admin/consultant/meeting/<int:mid>/cancel', methods=['POST'])
@perm_required('students')
def api_admin_consultant_cancel(mid):
    meeting = ConsultantMeeting.query.get_or_404(mid)
    if meeting.status != 'upcoming':
        return jsonify({'success':False, 'error':'Only upcoming meetings can be cancelled.'}), 400
    meeting.status = 'cancelled'
    meeting.status_note = 'Cancelled by admin.'
    _send_notif(meeting.student_id, 'Consultant Meeting Cancelled',
        f'Your meeting with {meeting.consultant.name} on {meeting.slot_label} has been cancelled by the school.',
        'warning', 'general')
    db.session.commit()
    return jsonify({'success':True})

@app.route('/api/admin/consultant/meeting/<int:mid>/reschedule', methods=['POST'])
@perm_required('students')
def api_admin_consultant_reschedule(mid):
    meeting = ConsultantMeeting.query.get_or_404(mid)
    d = request.json or {}
    new_slot = d.get('slot_label', '').strip()
    if meeting.status != 'upcoming':
        return jsonify({'success':False, 'error':'Only upcoming meetings can be changed.'}), 400
    if not new_slot:
        return jsonify({'success':False, 'error':'New time is required.'}), 400
    conflict = ConsultantMeeting.query.filter_by(
        consultant_id=meeting.consultant_id,
        slot_label=new_slot,
        status='upcoming'
    ).filter(ConsultantMeeting.id != meeting.id).first()
    if conflict:
        return jsonify({'success':False, 'error':'That slot is no longer available.'}), 400
    old_slot = meeting.slot_label
    meeting.slot_label = new_slot
    meeting.status_note = f'Rescheduled from {old_slot} by admin.'
    _send_notif(meeting.student_id, 'Consultant Meeting Updated',
        f'Your meeting with {meeting.consultant.name} has been changed from {old_slot} to {new_slot}.',
        'info', 'general')
    db.session.commit()
    return jsonify({'success':True})

@app.route('/api/admin/course/<int:cid>/action', methods=['POST'])
@perm_required('courses')
def api_admin_course_action(cid):
    course = Course.query.get_or_404(cid)
    d = request.json or {}
    action = d.get('action', '').strip()
    if action == 'end':
        course.is_active = False
        course.is_visible = True
        course.is_archived = False
        course.course_state = 'ended'
    elif action == 'hide':
        course.is_active = False
        course.is_visible = False
        course.is_archived = False
        course.course_state = 'hidden'
    elif action == 'archive':
        course.is_active = False
        course.is_visible = False
        course.is_archived = True
        course.course_state = 'archived'
    elif action == 'restore':
        course.is_active = True
        course.is_visible = True
        course.is_archived = False
        course.course_state = 'active'
    elif action == 'delete':
        Enrollment.query.filter_by(course_id=cid).delete()
        Attendance.query.filter_by(course_id=cid).delete()
        Penalty.query.filter_by(course_id=cid).delete()
        Exam.query.filter((Exam.course_id == cid) | (Exam.course_code == course.code)).delete(synchronize_session=False)
        Grade.query.filter_by(course_code=course.code).delete()
        db.session.delete(course)
        db.session.commit()
        return jsonify({'success':True})
    else:
        return jsonify({'success':False, 'error':'Unknown action.'}), 400
    db.session.commit()
    return jsonify({'success':True})

@app.route('/api/admin/enrollment/action', methods=['POST'])
@perm_required('courses')
def api_enrollment_action():
    d=request.json; eid=d.get('enrollment_id'); action=d.get('action')
    e=Enrollment.query.get_or_404(eid)
    if action in ('drop','withdraw'):
        prev_status=e.status; e.status=action+'ped' if action=='drop' else 'withdrawn'; e.is_approved=False
        e.drop_reason=d.get('reason','')
        if action == 'withdraw':
            _apply_withdrawal_to_course(e.student_id, e.course_id, e.drop_reason)
        db.session.add(Notification(student_id=e.student_id,
            title=f'Enrolment {"Dropped" if action=="drop" else "Withdrawn"}: {e.course.code}',
            body=f'You have been {"dropped from" if action=="drop" else "withdrawn from"} {e.course.code} — {e.course.title}.\n\nReason: {e.drop_reason}',
            notif_type='warning'))
        db.session.commit()
        if prev_status=='enrolled': _promote_waitlist(e.course_id)
    elif action=='waitlist':
        if e.status != 'enrolled':
            return jsonify({'success':False,'error':'Only enrolled students can be moved to waitlist.'}),400
        existing_waitlist=Enrollment.query.filter_by(course_id=e.course_id,status='waitlist').count()
        e.status='waitlist'; e.is_approved=False
        e.waitlist_pos=existing_waitlist+1
        e.drop_reason=d.get('reason','')
        db.session.add(Notification(student_id=e.student_id,
            title=f'Moved To Waitlist: {e.course.code}',
            body=f'You have been moved to the waitlist for {e.course.code} 鈥?{e.course.title}.\n\nReason: {e.drop_reason}',
            notif_type='warning'))
        db.session.commit()
        if existing_waitlist > 0:
            _promote_waitlist(e.course_id)
    elif action=='promote':
        e.status='enrolled'; e.waitlist_pos=None; e.is_approved=True
        remaining=Enrollment.query.filter_by(course_id=e.course_id,status='waitlist').order_by(Enrollment.waitlist_pos).all()
        for i,en in enumerate(remaining,1): en.waitlist_pos=i
        db.session.add(Notification(student_id=e.student_id,
            title=f'Enrolment Confirmed: {e.course.code}',
            body=f'You have been moved from the waitlist and are now enrolled in {e.course.code} — {e.course.title}.',
            notif_type='success'))
        db.session.commit()
    elif action=='accept':
        if e.status != 'enrolled':
            return jsonify({'success':False,'error':'Only enrolled requests can be accepted.'}),400
        e.is_approved=True
        db.session.add(Notification(student_id=e.student_id,
            title=f'Enrolment Approved: {e.course.code}',
            body=f'Your enrolment has been approved for {e.course.code} - {e.course.title}.',
            notif_type='success'))
        db.session.commit()
    else: return jsonify({'success':False,'error':'Unknown action'}),400
    return jsonify({'success':True})

@app.route('/api/admin/course/<int:cid>/add-student', methods=['POST'])
@perm_required('courses')
def api_course_add_student(cid):
    course = Course.query.get_or_404(cid)
    d = request.json
    student_id = int(d.get('student_id'))
    existing = Enrollment.query.filter_by(student_id=student_id, course_id=cid).first()
    if existing:
        if existing.status == 'enrolled':
            return jsonify({'success':False,'error':'Student is already enrolled.'}),400
        if existing.status == 'waitlist':
            existing.status = 'enrolled'
            existing.is_approved = True
            existing.waitlist_pos = None
            remaining = Enrollment.query.filter_by(course_id=cid, status='waitlist').order_by(Enrollment.waitlist_pos).all()
            for i, en in enumerate(remaining, 1):
                en.waitlist_pos = i
        else:
            existing.status = 'enrolled'
            existing.is_approved = True
            existing.waitlist_pos = None
    else:
        db.session.add(Enrollment(student_id=student_id, course_id=cid, status='enrolled', is_approved=True))
    _send_notif(student_id, f'Enrolment Confirmed: {course.code}',
        f'You have been added to {course.code} {course.title}.', 'success', 'general')
    db.session.commit()
    return jsonify({'success':True})

@app.route('/api/admin/notification/send', methods=['POST'])
@perm_required('notifications')
def api_notif_send():
    d=request.json
    db.session.add(Notification(student_id=d.get('student_id') or None,
        title=d['title'],body=d.get('body',''),notif_type=d.get('type','info')))
    db.session.commit(); return jsonify({'success':True})

@app.route('/api/admin/notification/delete/<int:nid>', methods=['POST'])
@perm_required('notifications')
def api_notif_delete(nid):
    n=Notification.query.get_or_404(nid); db.session.delete(n); db.session.commit()
    return jsonify({'success':True})


@app.route('/api/admin/document/upload/<int:did>', methods=['POST'])
@perm_required('documents')
def api_doc_admin_upload(did):
    doc = AcademicDocument.query.get_or_404(did)
    f = request.files.get('file')
    if not f or not f.filename:
        return jsonify({'success': False, 'message': 'No file selected.'})
    ext = f.filename.rsplit('.', 1)[-1].lower()
    if ext not in ['pdf', 'doc', 'docx', 'jpg', 'png', 'jpeg']:
        return jsonify({'success': False, 'message': 'File type not allowed.'})
    stored = f'{__import__("secrets").token_hex(8)}.{ext}'
    import os
    f.save(os.path.join(app.config['UPLOAD_FOLDER'], stored))
    size = os.path.getsize(os.path.join(app.config['UPLOAD_FOLDER'], stored))
    size_str = f'{size/1024:.1f} KB' if size < 1024*1024 else f'{size/1024/1024:.1f} MB'
    doc.filename = stored
    doc.original_name = f.filename
    doc.file_size = size_str
    doc.status = 'ready'
    s = Student.query.get(doc.student_id)
    if s:
        _send_notif(s.id,
            f'Document Ready: {doc.doc_type.replace("_"," ").title()}',
            f'Your requested document "{doc.doc_type.replace("_"," ").title()}" is ready for download.',
            'success', 'documents')
    db.session.commit()
    return jsonify({'success': True})

@app.route('/api/admin/document/update/<int:did>', methods=['POST'])
@perm_required('documents')
def api_doc_update(did):
    doc=AcademicDocument.query.get_or_404(did); d=request.json
    doc.status=d.get('status',doc.status)
    if doc.status=='rejected':
        reason=d.get('reject_reason','No reason provided.'); doc.reject_reason=reason
        s=Student.query.get(doc.student_id)
        if s:
            db.session.add(Notification(student_id=s.id,
                title=f'Document Request Declined: {doc.doc_type.replace("_"," ").title()}',
                body=f'Your document request has been declined.\n\nReason: {reason}\n\nContact Student Services for assistance.',
                notif_type='warning'))
    db.session.commit(); return jsonify({'success':True})

@app.route('/api/invoice/receipt/<int:iid>')
@student_required
def api_invoice_receipt(iid):
    inv = Invoice.query.get_or_404(iid)
    s = get_student()
    if inv.student_id != s.id or not inv.receipt_file:
        return jsonify({'error':'No receipt available.'}),404
    return send_from_directory(app.config['UPLOAD_FOLDER'], inv.receipt_file,
        as_attachment=True, download_name=inv.receipt_name or inv.receipt_file)

@app.route('/api/admin/exam/add', methods=['POST'])
@perm_required('exams')
def api_exam_add():
    d=request.json
    course=Course.query.filter_by(code=d.get('code',''), is_active=True).first()
    db.session.add(Exam(
        course_id=course.id if course else None,
        course_code=d['code'],
        course_title=course.title if course else d.get('title',''),
        exam_date=d['date'],start_time=d.get('start',''),end_time=d.get('end',''),
        room=d.get('room',''),notes=d.get('notes',''),
        semester=d.get('semester','')))
    db.session.commit(); return jsonify({'success':True})

@app.route('/api/admin/exam/delete/<int:eid>', methods=['POST'])
@perm_required('exams')
def api_exam_delete(eid):
    e=Exam.query.get_or_404(eid); db.session.delete(e); db.session.commit()
    return jsonify({'success':True})

@app.route('/api/admin/semester/add', methods=['POST'])
@perm_required('semesters')
def api_semester_add():
    d=request.json
    if Semester.query.filter_by(name=d['name']).first():
        return jsonify({'success':False,'error':'Semester already exists.'})
    s=Semester(name=d['name'],start_date=d.get('start_date',''),
        end_date=d.get('end_date',''),is_active=d.get('is_active',True))
    db.session.add(s); db.session.commit(); return jsonify({'success':True,'id':s.id})

@app.route('/api/admin/semester/update/<int:sid>', methods=['POST'])
@perm_required('semesters')
def api_semester_update(sid):
    s=Semester.query.get_or_404(sid); d=request.json
    for f in ['name','start_date','end_date','is_active','is_current']:
        if f in d: setattr(s,f,d[f])
    if d.get('is_current'):
        Semester.query.filter(Semester.id!=sid).update({'is_current':False})
    db.session.commit(); return jsonify({'success':True})

@app.route('/api/admin/semester/delete/<int:sid>', methods=['POST'])
@perm_required('semesters')
def api_semester_delete(sid):
    s=Semester.query.get_or_404(sid); db.session.delete(s); db.session.commit()
    return jsonify({'success':True})

@app.route('/api/admin/staff/add', methods=['POST'])
@perm_required('staff')
def api_staff_add():
    d=request.json
    if AdminUser.query.filter_by(username=d['username']).first():
        return jsonify({'success':False,'error':'Username already taken.'})
    db.session.add(AdminUser(username=d['username'],pw_hash=AdminUser.hash_pw(d.get('password','Staff2025!')),
        role=d.get('role','staff'),full_name=d.get('full_name',''),
        email=d.get('email',''),department=d.get('department','')))
    db.session.commit(); return jsonify({'success':True})

@app.route('/api/admin/staff/update/<int:uid>', methods=['POST'])
@perm_required('staff')
def api_staff_update(uid):
    u=AdminUser.query.get_or_404(uid); d=request.json
    for f in ['full_name','email','department','role','is_active']:
        if f in d: setattr(u,f,d[f])
    if d.get('reset_password'): u.pw_hash=AdminUser.hash_pw('Staff2025!')
    db.session.commit(); return jsonify({'success':True})

@app.route('/api/admin/penalty/add', methods=['POST'])
@perm_required('penalties')
def api_penalty_add():
    stored, original = _save_upload('evidence')
    cid = request.form.get('course_id') or None
    if cid: cid=int(cid)
    action_type = request.form.get('action_type','warning')
    p = Penalty(
        student_id=int(request.form.get('student_id')),
        course_id=cid,
        reported_by=session.get('admin_user',''),
        description=request.form.get('description',''),
        action_type=action_type,
        evidence_file=stored, evidence_name=original)
    db.session.add(p); db.session.flush()
    s=Student.query.get(p.student_id)
    if action_type == 'compulsory_withdrawal' and cid:
        _apply_withdrawal_to_course(p.student_id, cid, p.description)
    if s:
        db.session.add(Notification(student_id=s.id,
            title=f'Disciplinary Notice — {p.action_type.title()}',
            body=f'A {p.action_type} has been issued against your account.\n\n{p.description}\n\nYou may submit an appeal within 48 hours.',
            notif_type='warning'))
    db.session.commit(); return jsonify({'success':True,'id':p.id})

@app.route('/api/admin/penalty/resolve/<int:pid>', methods=['POST'])
@perm_required('penalties')
def api_penalty_resolve(pid):
    p=Penalty.query.get_or_404(pid); d=request.json
    p.status=d.get('status','resolved')
    db.session.commit(); return jsonify({'success':True})

@app.route('/api/admin/appeal/review/<int:aid>', methods=['POST'])
@perm_required('penalties')
def api_appeal_review(aid):
    appeal=PenaltyAppeal.query.get_or_404(aid); d=request.json
    appeal.status=d.get('status','accepted')
    appeal.reviewed_by=session.get('admin_user','')
    appeal.review_note=d.get('note','')
    if appeal.status=='accepted':
        appeal.penalty.status='overturned'
        db.session.add(Notification(student_id=appeal.student_id,
            title='Appeal Accepted',
            body=f'Your appeal has been accepted. The penalty has been overturned.\n\n{appeal.review_note}',
            notif_type='success'))
    else:
        appeal.penalty.status='active'
        db.session.add(Notification(student_id=appeal.student_id,
            title='Appeal Decision',
            body=f'Your appeal has been reviewed and was not upheld.\n\n{appeal.review_note}',
            notif_type='warning'))
    db.session.commit(); return jsonify({'success':True})

@app.route('/api/admin/penalty/evidence/<int:pid>')
@admin_required
def api_penalty_evidence(pid):
    p=Penalty.query.get_or_404(pid)
    if p.evidence_file:
        return send_from_directory(app.config['UPLOAD_FOLDER'], p.evidence_file,
            as_attachment=True, download_name=p.evidence_name or p.evidence_file)
    return jsonify({'error':'No file'}),404

@app.route('/api/admin/appeal/evidence/<int:aid>')
@admin_required
def api_appeal_evidence(aid):
    a=PenaltyAppeal.query.get_or_404(aid)
    if a.evidence_file:
        return send_from_directory(app.config['UPLOAD_FOLDER'], a.evidence_file,
            as_attachment=True, download_name=a.evidence_name or a.evidence_file)
    return jsonify({'error':'No file'}),404

@app.route('/api/admin/message/send', methods=['POST'])
@perm_required('messages')
def api_message_send():
    return jsonify({'success':False, 'error':'Messages has been disabled.'}), 410

@app.route('/api/admin/messages/poll')
@admin_required
def api_messages_poll():
    return jsonify([])

@app.route('/api/admin/profile-request/review/<int:rid>', methods=['POST'])
@perm_required('students')
def api_profile_request_review(rid):
    req = ProfileChangeRequest.query.get_or_404(rid)
    if req.status != 'pending':
        return jsonify({'success':False,'error':'Request already reviewed.'}),400
    d = request.json or {}
    status = d.get('status', 'approved')
    note = d.get('note', '').strip()
    req.status = status
    req.reviewed_at = datetime.utcnow()
    req.reviewed_by = session.get('admin_user', '')
    req.admin_note = note
    payload = _profile_request_payload(req)
    if status == 'approved':
        for field, value in payload.items():
            if field != 'student_id' and hasattr(req.student, field):
                setattr(req.student, field, value)
        _send_notif(req.student_id, 'Profile Update Approved',
            'Your requested profile changes have been approved and applied.',
            'success', 'general')
    else:
        _send_notif(req.student_id, 'Profile Update Not Approved',
            f'Your requested profile changes were not approved.\n\nReason: {note or "No reason provided."}',
            'warning', 'general')
    db.session.commit()
    return jsonify({'success':True})

@app.route('/api/admin/ticket/update/<int:tid>', methods=['POST'])
@perm_required('tickets')
def api_ticket_update(tid):
    t=Ticket.query.get_or_404(tid); d=request.json
    if 'status' in d: t.status=d['status']
    if 'reply_text' in d: t.reply_text=d['reply_text']; t.replied_by=session.get('admin_user','')
    t.updated_at=datetime.utcnow(); db.session.commit()
    return jsonify({'success':True})

@app.route('/api/admin/ticket/reply-file/<int:tid>', methods=['POST'])
@perm_required('tickets')
def api_ticket_reply_file(tid):
    t=Ticket.query.get_or_404(tid)
    stored,original=_save_upload('file')
    if stored: t.reply_file=stored; t.reply_fname=original; t.replied_by=session.get('admin_user',''); t.updated_at=datetime.utcnow(); db.session.commit()
    return jsonify({'success':True})

@app.route('/api/ticket/attachment/<int:tid>')
@student_required
def api_ticket_attachment(tid):
    t=Ticket.query.get_or_404(tid)
    if t.reply_file:
        return send_from_directory(app.config['UPLOAD_FOLDER'], t.reply_file,
            as_attachment=True, download_name=t.reply_fname or t.reply_file)
    return jsonify({'error':'No file'}),404

@app.route('/api/admin/admissions/update/<int:app_id>', methods=['POST'])
@perm_required('admissions')
def api_admissions_update(app_id):
    app_obj=Application.query.get_or_404(app_id); d=request.json
    ns=d.get('status')
    if ns not in ['pending','reviewing','approved','rejected']: return jsonify({'success':False}),400
    app_obj.status=ns; app_obj.admin_notes=d.get('notes',app_obj.admin_notes or '')
    app_obj.reviewed_at=datetime.utcnow(); app_obj.reviewed_by=session.get('admin_user')
    db.session.commit(); return jsonify({'success':True,'status':app_obj.status,'label':app_obj.status_label()})

@app.route('/api/admin/admissions/bulk', methods=['POST'])
@perm_required('admissions')
def api_admissions_bulk():
    d=request.json; ids=d.get('ids',[]); action=d.get('action')
    sm={'approve':'approved','reject':'rejected','review':'reviewing'}
    ns=sm.get(action)
    if not ns: return jsonify({'success':False}),400
    Application.query.filter(Application.id.in_(ids)).update({'status':ns,'reviewed_at':datetime.utcnow(),'reviewed_by':session.get('admin_user')},synchronize_session=False)
    db.session.commit(); return jsonify({'success':True,'count':len(ids)})

@app.route('/api/admin/admissions/convert/<int:app_id>', methods=['POST'])
@perm_required('admissions')
def api_admissions_convert(app_id):
    app_obj=Application.query.get_or_404(app_id)
    if app_obj.status!='approved': return jsonify({'success':False,'error':'Must be approved first.'}),400
    if app_obj.linked_student_id: return jsonify({'success':False,'error':'Account already created.'}),400
    existing=Student.query.filter_by(email=app_obj.email).first()
    if existing:
        app_obj.linked_student_id=existing.id; db.session.commit()
        return jsonify({'success':True,'student_id':existing.student_id,'existing':True})
    sid=f"CNA{secrets.randbelow(900000)+100000}"
    s=Student(student_id=sid,first_name=app_obj.first_name,last_name=app_obj.last_name,
        email=app_obj.email,phone=app_obj.phone or '',dob=app_obj.dob or '',
        address=f"{app_obj.address}, {app_obj.city}, {app_obj.province}",
        program='OSSD — Ontario Secondary School Diploma',year_of_study=1,status='active',
        pw_hash=Student.hash_pw('123456'))
    db.session.add(s); db.session.flush()
    app_obj.linked_student_id=s.id
    db.session.add(Notification(student_id=s.id,
        title=f'Welcome to Canada Northstar Academy, {s.first_name}!',
        body=f'Your application ({app_obj.app_number}) has been approved. Student ID: {sid}. Default password: 123456 — please change it after first login.',
        notif_type='success'))
    db.session.commit()
    return jsonify({'success':True,'student_id':sid,'id':s.id,'existing':False})

@app.route('/api/admin/stats')
@admin_required
def api_stats():
    by_program=db.session.query(Student.program,db.func.count()).group_by(Student.program).all()
    by_year=db.session.query(Student.year_of_study,db.func.count()).group_by(Student.year_of_study).all()
    by_status=db.session.query(Student.status,db.func.count()).group_by(Student.status).all()
    return jsonify({'by_program':dict(by_program),'by_year':{str(k):v for k,v in by_year},'by_status':dict(by_status)})

@app.route('/api/courses/active')
@admin_required
def api_active_courses():
    courses = Course.query.filter_by(is_active=True).order_by(Course.code).all()
    return jsonify([{'id':c.id,'code':c.code,'title':c.title,'semester':c.semester} for c in courses])

# ══════════════════════════════════════════════════════════════════
#  SEED
# ══════════════════════════════════════════════════════════════════

def init_db():
    with app.app_context():
        db.create_all()
        _ensure_schema_updates()

        if not AdminUser.query.first():
            for u in [
                AdminUser(username='admin',     pw_hash=AdminUser.hash_pw('acorn2025'),   role='superadmin', full_name='System Administrator', email='admin@northstar.ca',  department='Administration', is_active=True),
                AdminUser(username='principal', pw_hash=AdminUser.hash_pw('prin2025'),    role='admin',      full_name='Dr. Sarah Wong',        email='swong@northstar.ca',  department='Administration', is_active=True),
                AdminUser(username='mchen',     pw_hash=AdminUser.hash_pw('teacher2025'), role='teacher',    full_name='Mr. Michael Chen',      email='mchen@northstar.ca',  department='Computer Science', is_active=True),
                AdminUser(username='lreyes',    pw_hash=AdminUser.hash_pw('teacher2025'), role='teacher',    full_name='Ms. Laura Reyes',       email='lreyes@northstar.ca', department='Mathematics', is_active=True),
                AdminUser(username='jsmith',    pw_hash=AdminUser.hash_pw('staff2025'),   role='staff',      full_name='Jennifer Smith',        email='jsmith@northstar.ca', department='Student Services', is_active=True),
            ]: db.session.add(u)
            db.session.commit(); print("✓ Staff accounts")

        if not Semester.query.first():
            for sem in [
                Semester(name='Fall 2023',   start_date='2023-09-05', end_date='2023-12-22', is_active=False, is_current=False),
                Semester(name='Winter 2024', start_date='2024-01-08', end_date='2024-04-26', is_active=False, is_current=False),
                Semester(name='Fall 2024',   start_date='2024-09-03', end_date='2024-12-20', is_active=False, is_current=False),
                Semester(name='Winter 2025', start_date='2025-01-06', end_date='2025-04-25', is_active=True,  is_current=True),
                Semester(name='Fall 2025',   start_date='2025-09-02', end_date='2025-12-19', is_active=True,  is_current=False),
            ]: db.session.add(sem)
            db.session.commit(); print("✓ Semesters")

        if Student.query.count(): return

        students_data=[
            ("Alex","Chen","alex.chen@northstar.ca","416-555-0182","CNA100823",3,"55 Maple Ave, Toronto ON","Li Chen","416-555-0199"),
            ("Emma","Zhang","emma.zhang@northstar.ca","647-555-0201","CNA100782",2,"120 Oak St, Mississauga ON","Wei Zhang","647-555-0200"),
            ("James","Park","james.park@northstar.ca","905-555-0312","CNA100934",4,"45 Birch Cres, Richmond Hill ON","Soo Park","905-555-0311"),
        ]
        students=[]
        for fn,ln,email,ph,sid,yr,addr,en,ep in students_data:
            s=Student(first_name=fn,last_name=ln,email=email,phone=ph,student_id=sid,
                      program='OSSD — Ontario Secondary School Diploma',year_of_study=yr,
                      address=addr,emergency_name=en,emergency_phone=ep,pw_hash=Student.hash_pw('123456'))
            db.session.add(s); students.append(s)
        db.session.commit()

        courses_data=[
            ("ENG3U","English: Understanding Contemporary First Nations, Métis, and Inuit Voices","Mon,Wed,Fri","2025-01-06","2025-04-25","09:00","10:00","RM 201","Ms. Patricia Hall","1.0","English","blue",30,"Winter 2025","Explores Indigenous literature, oral traditions, and contemporary voices. Develops critical reading and analytical writing."),
            ("MCF3M","Functions and Applications, Grade 11","Tue,Thu","2025-01-06","2025-04-25","10:00","11:30","RM 105","Ms. Laura Reyes","1.0","Mathematics","gold",28,"Winter 2025","Quadratic and exponential functions, trigonometry, and real-world applications."),
            ("SPH3U","Physics, Grade 11, University Preparation","Mon,Wed,Fri","2025-01-06","2025-04-25","11:00","12:00","RM 203","Dr. James Lee","1.0","Science","teal",24,"Winter 2025","Kinematics, dynamics, energy, momentum, waves, and fields."),
            ("ICS3U","Introduction to Computer Science, Grade 11","Tue,Thu","2025-01-06","2025-04-25","13:00","14:30","RM 106","Mr. Michael Chen","1.0","Computer Science","purple",28,"Winter 2025","Programming with Python: algorithms, data structures, OOP, and software design."),
            ("SCH3U","Chemistry, Grade 11, University Preparation","Mon,Wed,Fri","2025-01-06","2025-04-25","13:00","14:00","RM 204","Dr. Amy Liu","1.0","Science","red",25,"Winter 2025","Matter, bonding, reactions, thermochemistry, solutions, and electrochemistry."),
            ("CHC2D","Canadian History Since World War I, Grade 10","Tue,Thu","2025-01-06","2025-04-25","09:00","10:30","RM 302","Mr. David Park","1.0","Social Science","green",22,"Winter 2025","Canada's political, social, and economic development from WWI to present."),
        ]
        courses=[]
        for code,title,wdays,ps,pe,ts,te,room,instr,cr,dept,color,cap,sem,desc in courses_data:
            c=Course(code=code,title=title,weekdays=wdays,period_start=ps,period_end=pe,
                     time_start=ts,time_end=te,room=room,instructor=instr,credits=cr,
                     department=dept,color=color,capacity=cap,semester=sem,description=desc)
            db.session.add(c); courses.append(c)
        db.session.commit()

        alex=students[0]
        for code in ["ENG3U","MCF3M","SPH3U"]:
            c=Course.query.filter_by(code=code).first()
            if c: db.session.add(Enrollment(student_id=alex.id,course_id=c.id,status='enrolled'))
        for code in ["ICS3U","SCH3U"]:
            c=Course.query.filter_by(code=code).first()
            if c: db.session.add(Enrollment(student_id=alex.id,course_id=c.id,status='wishlist'))
        # CHC2D added to wishlist (not waitlist - course is open)
        c_wl=Course.query.filter_by(code="CHC2D").first()
        if c_wl: db.session.add(Enrollment(student_id=alex.id,course_id=c_wl.id,status='wishlist'))
        for idx,s in enumerate(students[1:],1):
            for c in courses[idx:idx+3]: db.session.add(Enrollment(student_id=s.id,course_id=c.id,status='enrolled'))
        db.session.commit()

        grades_data=[
            # Grade 9 (Fall 2022 / Winter 2023)
            ("ENG1D1","English, Grade 9, Academic","Fall 2022",84.0,"1.0"),
            ("MPM1D1","Principles of Mathematics, Grade 9","Fall 2022",79.5,"1.0"),
            ("SNC1D1","Science, Grade 9, Academic","Fall 2022",82.0,"1.0"),
            ("CGC1D1","Issues in Canadian Geography, Grade 9","Fall 2022",88.0,"1.0"),
            ("ART1O1","Visual Arts, Grade 9","Winter 2023",91.0,"1.0"),
            ("PPL1O1","Healthy Active Living, Grade 9","Winter 2023",85.0,"NA"),
            ("GLE1O1","Learning Strategies, Grade 9","Winter 2023",90.0,"0.5"),
            # Grade 10 (Fall 2023 / Winter 2024)
            ("ENG2D1","English, Grade 10, Academic","Fall 2023",82.5,"1.0"),
            ("MPM2D1","Principles of Mathematics, Grade 10","Fall 2023",78.3,"1.0"),
            ("SNC2D1","Science, Grade 10, Academic","Fall 2023",85.6,"1.0"),
            ("CHC2D1","Canadian History Since World War I","Fall 2023",79.2,"1.0"),
            ("TGJ2O1","Communications Technology, Grade 10","Winter 2024",91.5,"1.0"),
            ("AVI2O1","Visual Arts, Grade 10","Winter 2024",93.0,"1.0"),
            ("PPL2O1","Healthy Active Living, Grade 10","Winter 2024",87.0,"1.0"),
            # Grade 11 (Fall 2024 / Winter 2025 — in progress represented as final here)
            ("ENG3U1","English, Grade 11, University Prep","Fall 2024",88.0,"1.0"),
            ("MCF3M1","Functions and Applications, Grade 11","Fall 2024",81.5,"1.0"),
            ("SBI3U1","Biology, Grade 11, University Prep","Fall 2024",76.0,"1.0"),
            ("CHW3M1","World History, Grade 11","Fall 2024",83.0,"1.0"),
            ("ICS3U1","Introduction to Computer Science, Grade 11","Winter 2024",88.5,"1.0"),
            ("BOH4M1","Business Leadership, Grade 12","Winter 2024",85.0,"1.0"),
            ("FSF3U1","Core French, Grade 11","Winter 2024",74.0,"1.0"),
            ("HSP3U1","Introduction to Anthropology, Psych & Sociology","Winter 2024",91.0,"1.0"),
        ]
        for s in students:
            for code,title,sem,pct,cr in grades_data:
                db.session.add(Grade(student_id=s.id,course_code=code,course_title=title,semester=sem,percentage=pct,credits=cr))
        db.session.commit()

        invoices_data=[
            ("Tuition — Winter 2025",6340.00,"2025-02-01","Winter 2025","paid"),
            ("Student Activity Fee",120.00,"2025-02-01","Winter 2025","paid"),
            ("Technology Fee",200.00,"2025-02-01","Winter 2025","paid"),
            ("Health & Dental Insurance",312.00,"2025-01-15","Winter 2025","outstanding"),
            ("Tuition — Fall 2024",6100.00,"2024-10-01","Fall 2024","paid"),
        ]
        for s in students:
            for desc,amt,due,sem,status in invoices_data:
                inv=Invoice(student_id=s.id,description=desc,amount=amt,due_date=due,semester=sem,status=status)
                if status=='paid': inv.paid_at=datetime.utcnow(); inv.payment_method='Manual Transfer'
                db.session.add(inv)
        db.session.commit()

        cs401=Course.query.filter_by(code="ENG3U").first()
        math301=Course.query.filter_by(code="MCF3M").first()
        eng301=Course.query.filter_by(code="SPH3U").first()
        for code,course,d,s,e,room,notes in [
            ("ENG3U",cs401,"2025-04-15","09:00","12:00","EX 101","Bring student ID"),
            ("MCF3M",math301,"2025-04-18","14:00","17:00","EX 102","Formula sheet provided"),
            ("SPH3U",eng301,"2025-04-22","09:00","12:00","EX 103","Open book"),
        ]:
            db.session.add(Exam(course_id=course.id if course else None,
                course_code=code,course_title=course.title if course else '',
                exam_date=d,start_time=s,end_time=e,room=room,notes=notes,semester='Winter 2025'))
        db.session.commit()

        for sid,title,body,typ in [
            (None,"Winter 2025 Exam Schedule Posted","Final exam schedules are now available.","info"),
            (1,"Outstanding Balance Reminder","You have $312.00 outstanding. Please pay by January 15.","warning"),
            (None,"Winter 2025 Progress Reports","Mid-term progress reports are now available.","success"),
            (None,"School Closure — Family Day","CNA will be closed Monday, February 17 for Family Day.","info"),
            (1,"Welcome to CNA!","Welcome to Canada Northstar Academy! Student ID: CNA100823. Default password: 123456.","success"),
        ]: db.session.add(Notification(student_id=sid,title=title,body=body,notif_type=typ))
        db.session.commit()

        for title,due,course,priority in [
            ("CS401 Assignment 3","2025-02-10","ENG3U","high"),
            ("MATH301 Midterm Study","2025-02-18","MCF3M","high"),
            ("ENG301 Essay Draft","2025-02-05","SPH3U","medium"),
        ]: db.session.add(Todo(student_id=1,title=title,due_date=due,course=course,priority=priority))
        db.session.commit()

        if Application.query.count()==0:
            def _gen(): return f"NSA-2025-{secrets.token_hex(3).upper()}"
            for a in [
                dict(first_name='Emma',last_name='Thompson',email='emma.t@gmail.com',phone='416-555-0101',dob='2008-03-15',gender='Female',citizenship='Canadian',first_language='English',applying_grade='Grade 10',entry_term='September 2025',current_school='Hillside Middle School',current_grade='Grade 9',gpa='88%',parent_name='Sarah Thompson',parent_email='sarah.thompson@gmail.com',parent_phone='416-555-0100',parent_relation='Mother',address='123 Maple Ave',city='Toronto',province='Ontario',postal_code='M4B 1B3',country='Canada',essay='I am passionate about STEM and believe CNA will help me reach my goals.',activities='Swimming, Math club, Volunteer',how_heard='School counsellor',status='pending'),
                dict(first_name='Liam',last_name='Zhang',email='liam.zhang@outlook.com',phone='647-555-0202',dob='2007-07-22',gender='Male',citizenship='Canadian PR',first_language='Mandarin',applying_grade='Grade 11',entry_term='September 2025',current_school='Westview Secondary',current_grade='Grade 10',gpa='92%',parent_name='Wei Zhang',parent_email='wei.zhang@gmail.com',parent_phone='647-555-0200',parent_relation='Father',address='456 Oak St',city='Mississauga',province='Ontario',postal_code='L5A 3Z2',country='Canada',essay='Strong academic discipline will serve me well at CNA.',activities='Chess, Robotics, Piano Grade 8',how_heard='Friend referral',status='reviewing'),
                dict(first_name='Sofia',last_name='Patel',email='sofia.patel@gmail.com',phone='905-555-0303',dob='2006-11-08',gender='Female',citizenship='Canadian',first_language='English',applying_grade='Grade 12',entry_term='September 2025',current_school='Richmond Hill High',current_grade='Grade 11',gpa='95%',parent_name='Raj Patel',parent_email='raj.patel@gmail.com',parent_phone='905-555-0300',parent_relation='Father',address='789 Birch Cres',city='Richmond Hill',province='Ontario',postal_code='L4C 5T7',country='Canada',essay="My dream is medicine. CNA's rigorous program will help me reach that goal.",activities='Debate, Hospital volunteer, Track',how_heard='Online search',status='approved'),
            ]:
                status=a.pop('status'); obj=Application(app_number=_gen(),status=status,**a)
                if status!='pending': obj.reviewed_at=datetime.utcnow(); obj.reviewed_by='admin'
                db.session.add(obj)
            db.session.commit()

        # Sample message
        if not GraduationWindow.query.first():
            db.session.add(GraduationWindow(is_open=False,graduation_date='2025-06-20',
                deadline='2025-04-30',min_credits=20.0,
                notes='OSSD Graduation Ceremony 2025. Minimum 20 credits required.',
                created_by='admin'))
            db.session.commit()

        if not Message.query.first():
            db.session.add(Message(sender='admin',body='Welcome to the CNA Staff Hub! Post messages here.'))
            db.session.add(Message(sender='mchen',body='ICS3U midterm results are in — class average 81%.'))
            db.session.commit()

        print("✓ V7 database seeded")

@app.route('/api/admin/student/add-credits', methods=['POST'])
@perm_required('grades')
def api_student_add_credits():
    d=request.json; s=Student.query.get_or_404(d['student_id'])
    amount=float(d.get('amount',0)); reason=d.get('reason','').strip()
    if not reason: return jsonify({'success':False,'error':'Reason required.'})
    s.extra_credits = round((s.extra_credits or 0) + amount, 1)
    s.extra_credits_reason = ((s.extra_credits_reason or '') + f"\n+{amount} ({datetime.utcnow().strftime('%Y-%m-%d')}): {reason}").strip()
    _send_notif(s.id,'Credits Updated',f'{amount} credit(s) added. Reason: {reason}\nTotal credits: {s.earned_credits()}','info','grade_added')
    db.session.commit()
    return jsonify({'success':True,'new_total':s.earned_credits()})

@app.route('/api/admin/graduation/window', methods=['POST'])
@perm_required('graduate')
def api_grad_window():
    d = request.json
    # Always get the most recent window (regardless of open/closed state)
    gw = GraduationWindow.query.order_by(GraduationWindow.id.desc()).first()
    if not gw:
        gw = GraduationWindow(created_by=session.get('admin_user',''))
        db.session.add(gw)
    was_open = gw.is_open
    for f in ['is_open','graduation_date','deadline','min_credits','notes']:
        if f in d: setattr(gw, f, d[f])
    gw.created_by = session.get('admin_user','')
    db.session.flush()
    # Notify eligible students when first opening
    if d.get('is_open') and not was_open:
        eligible = [s for s in Student.query.filter_by(status='active').all()
                    if s.earned_credits() >= (gw.min_credits or 20)]
        for s in eligible:
            _send_notif(s.id, 'Graduation Applications Now Open',
                f'Apply to graduate by {gw.deadline}. Graduation date: {gw.graduation_date}.',
                'success', 'graduation')
    db.session.commit()
    return jsonify({'success': True})

@app.route('/api/graduation/apply', methods=['POST'])
@student_required
def api_grad_apply():
    s = get_student()
    gw = GraduationWindow.query.order_by(GraduationWindow.id.desc()).first()
    if not _graduation_window_is_open(gw):
        return jsonify({'success':False,'message':'Applications are not currently open.'})
    if s.earned_credits() < gw.min_credits:
        return jsonify({'success':False,'message':f'Need {gw.min_credits} credits. You have {s.earned_credits()}.'})
    if GraduationApplication.query.filter_by(student_id=s.id,window_id=gw.id).first():
        return jsonify({'success':False,'message':'Already applied.'})
    db.session.add(GraduationApplication(student_id=s.id,window_id=gw.id,credits_at_time=s.earned_credits()))
    _send_notif(s.id,'Graduation Application Submitted',f'Your application has been received. Graduation date: {gw.graduation_date}.','success','graduation')
    db.session.commit(); return jsonify({'success':True,'message':'Application submitted!'})

@app.route('/api/admin/graduation/review/<int:gaid>', methods=['POST'])
@perm_required('graduate')
def api_grad_review(gaid):
    ga=GraduationApplication.query.get_or_404(gaid); d=request.json
    ga.status=d.get('status','approved'); ga.reviewed_at=datetime.utcnow()
    ga.reviewed_by=session.get('admin_user',''); ga.admin_note=d.get('note','')
    s=ga.student
    if ga.status=='approved':
        if s:
            s.status = 'graduated'
            _send_notif(s.id,'Graduation Application Approved! 🎓',
                f'Congratulations! Your graduation has been approved. Graduation date: {ga.window.graduation_date if ga.window else "TBD"}.',
                'success','graduation')
    else:
        if s: _send_notif(s.id,'Graduation Update',
            f'Your graduation application was not approved at this time.\n\n{ga.admin_note}','warning','graduation')
    db.session.commit(); return jsonify({'success':True})

@app.route('/api/admin/graduation/graduate/<int:gaid>', methods=['POST'])
@perm_required('graduate')
def api_grad_set_graduated(gaid):
    ga=GraduationApplication.query.get_or_404(gaid)
    if ga.status!='approved': return jsonify({'success':False,'error':'Must be approved.'}),400
    if ga.student: ga.student.status='graduated'
    ga.status='graduated'; db.session.commit(); return jsonify({'success':True})

@app.route('/admin/graduate')
@app.route('/admin/graduation')
@perm_required('graduate')
def admin_graduate():
    badges=_admin_badge_counts()
    gw=GraduationWindow.query.order_by(GraduationWindow.created_at.desc()).first()
    apps=GraduationApplication.query.order_by(GraduationApplication.submitted_at.desc()).all()
    return render_template('admin/graduation.html', gw=gw, apps=apps,
        badges=badges, admin=session.get('admin_user'), user=get_admin())

@app.route('/api/admin/gradebook/add', methods=['POST'])
@perm_required('grades')
def api_gradebook_add():
    d = request.json
    c = Course.query.get(d.get('course_id'))
    grade_title = (d.get('grade_title') or 'overall').lower()
    percentage = float(d['percentage'])
    enrollment = Enrollment.query.filter_by(student_id=d['student_id'], course_id=d.get('course_id')).first()
    credits = _normalize_grade_credits(c.credits if c else d.get('credits', '1.0'), percentage, enrollment.status if enrollment else None, grade_title)
    existing = Grade.query.filter_by(student_id=d['student_id'], course_code=d['course_code'], grade_title=grade_title).first()
    if existing:
        existing.percentage = percentage
        if d.get('semester'): existing.semester = d['semester']
        existing.course_title = c.title if c else existing.course_title
        existing.credits = credits
        existing.status = grade_title
    else:
        db.session.add(Grade(student_id=d['student_id'], course_code=d['course_code'],
            course_title=c.title if c else '', semester=d.get('semester',''),
            grade_title=grade_title, percentage=percentage, credits=credits, status=grade_title))
    _sync_course_grade_credits(d['student_id'], d['course_code'], percentage)
    s = Student.query.get(d['student_id'])
    if s:
        grade_title_label = _grade_label(grade_title)
        _send_notif(s.id, f'Grade Updated: {d["course_code"]} {grade_title_label}',
            f'Your {grade_title_label.lower()} grade for {d["course_code"]} has been updated to {d["percentage"]}%.', 'info', 'grade_added')
    db.session.commit()
    return jsonify({'success': True})

@app.route('/api/admin/attendance/add', methods=['POST'])
@perm_required('courses')
def api_attendance_add():
    d=request.json
    db.session.add(Attendance(student_id=d['student_id'],course_id=d['course_id'],
        date=d.get('date',''),time=d.get('time',''),type=d.get('type','absent'),
        duration=float(d.get('duration',1.0)),recorded_by=session.get('admin_user','')))
    s=Student.query.get(d['student_id']); c=Course.query.get(d['course_id'])
    if s and c:
        _send_notif(s.id,f'Attendance Record: {c.code}',
            f'Recorded on {d.get("date","")}: {d.get("type","").title()} · {d.get("duration",1)}h','warning','general')
        _ensure_attendance_warning(s.id, c.id)
    db.session.commit(); return jsonify({'success':True})

@app.route('/api/admin/attendance/delete/<int:aid>', methods=['POST'])
@perm_required('courses')
def api_attendance_delete(aid):
    a=Attendance.query.get_or_404(aid); db.session.delete(a); db.session.commit()
    return jsonify({'success':True})

@app.route('/api/penalty/accept', methods=['POST'])
@student_required
def api_penalty_accept():
    pid=request.json.get('penalty_id'); s=get_student(); p=Penalty.query.get_or_404(pid)
    if p.student_id!=s.id: return jsonify({'success':False}),403
    if p.status!='active': return jsonify({'success':False,'message':'Cannot accept.'})
    p.status='accepted'; db.session.commit()
    return jsonify({'success':True,'message':'Penalty accepted.'})

@app.route('/api/notif-settings/toggle', methods=['POST'])
@student_required
def api_notif_settings_toggle():
    s=get_student(); d=request.json
    key=d.get('key'); enabled=d.get('enabled',True)
    ns=NotifSetting.query.filter_by(student_id=s.id,notif_key=key).first()
    if ns: ns.enabled=enabled
    else: db.session.add(NotifSetting(student_id=s.id,notif_key=key,enabled=enabled))
    db.session.commit(); return jsonify({'success':True})

@app.route('/api/admin/exam/update/<int:eid>', methods=['POST'])
@perm_required('exams')
def api_exam_update(eid):
    e=Exam.query.get_or_404(eid); d=request.json
    course=Course.query.filter_by(code=d.get('code',''),is_active=True).first()
    if course: e.course_id=course.id; e.course_title=course.title
    e.course_code=d.get('code',e.course_code)
    for f,k in [('exam_date','date'),('start_time','start'),('end_time','end'),('room','room'),('notes','notes'),('semester','semester')]:
        if k in d: setattr(e,f,d[k])
    db.session.commit(); return jsonify({'success':True})

with app.app_context():
    db.create_all()
    _ensure_schema_updates()

if __name__ == '__main__':
    init_db()
    app.run(debug=True, port=5050)
