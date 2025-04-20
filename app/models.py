# app/models.py
from datetime import datetime, timezone
from app import db, bcrypt
# Import bcrypt later when needed for passwords
# from flask_bcrypt import Bcrypt
# bcrypt = Bcrypt()

class User(db.Model):
    __tablename__ = 'users'
    user_id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False, index=True)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=True) # Nullable for local OAuth
    google_id = db.Column(db.String(255), unique=True, nullable=True, index=True) #for google auth
    bankroll = db.Column(db.Numeric(12, 2), nullable=False, default=1000.00)
    is_email_verified = db.Column(db.Boolean, default=False)
    registration_date = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)) # Use timezone.utc
    last_login = db.Column(db.DateTime(timezone=True), nullable=True)
    active = db.Column(db.Boolean, default=True)
    

    # Relationships (will be added/used later)
    # bets = db.relationship('Bet', backref='user', lazy=True)
    # bankroll_history = db.relationship('BankrollHistory', backref='user', lazy=True)

    def __repr__(self):
        return f"<User {self.username}>"

    # Add password hashing/checking methods later
    def set_password(self, password):
        """Hashes the password and stores it"""
        self.password_hash = bcrypt.generate_password_hash(password).decode('utf-8')

    def check_password(self, password):
        """Checks if the provided password matches the stored hash"""
        if not self.password_hash: # User might only have Google login
             return False
        return bcrypt.check_password_hash(self.password_hash, password)
    
    @classmethod
    def find_by_email(cls, email):
        return cls.query.filter_by(email=email).first()

    @classmethod
    def find_by_username(cls, username):
        return cls.query.filter_by(username=username).first()

    @classmethod
    def find_by_google_id(cls, google_id):
        return cls.query.filter_by(google_id=google_id).first()

    def save_to_db(self):
        db.session.add(self)
        db.session.commit()

    def __repr__(self):
        return f"<User {self.username}>"
    

class Round(db.Model):
    __tablename__ = 'rounds'
    round_id = db.Column(db.Integer, primary_key=True)
    round_number = db.Column(db.Integer, nullable=False)
    year = db.Column(db.Integer, nullable=False)
    start_date = db.Column(db.DateTime(timezone=True), nullable=False)
    end_date = db.Column(db.DateTime(timezone=True), nullable=False)
    status = db.Column(db.String(20), nullable=False, default='Upcoming', index=True) # Upcoming, Active, Completed

    matches = db.relationship('Match', backref='round', lazy='dynamic') # Use dynamic for large collections

    __table_args__ = (db.UniqueConstraint('round_number', 'year', name='uq_round_number_year'),)

    def __repr__(self):
        return f"<Round {self.year} R{self.round_number} ({self.status})>"

    def to_dict(self):
         return {
            'round_id': self.round_id,
            'round_number': self.round_number,
            'year': self.year,
            'start_date': self.start_date.isoformat() if self.start_date else None,
            'end_date': self.end_date.isoformat() if self.end_date else None,
            'status': self.status,
        }


class Match(db.Model):
    __tablename__ = 'matches'
    match_id = db.Column(db.Integer, primary_key=True)
    external_match_id = db.Column(db.String(100), unique=True, nullable=True, index=True) # ID from scraper source
    round_id = db.Column(db.Integer, db.ForeignKey('rounds.round_id'), nullable=False, index=True)
    home_team = db.Column(db.String(100), nullable=False)
    away_team = db.Column(db.String(100), nullable=False)
    start_time = db.Column(db.DateTime(timezone=True), nullable=False, index=True)
    home_odds = db.Column(db.Numeric(6, 3), nullable=True)
    away_odds = db.Column(db.Numeric(6, 3), nullable=True)
    status = db.Column(db.String(20), nullable=False, default='Scheduled', index=True) # Scheduled, Live, Completed, Postponed, Cancelled
    result_home_score = db.Column(db.Integer, nullable=True)
    result_away_score = db.Column(db.Integer, nullable=True)
    winner = db.Column(db.String(100), nullable=True) # Home Team Name, Away Team Name, or 'Draw'
    last_odds_update = db.Column(db.DateTime(timezone=True), nullable=True)

    # Relationships (will be added/used later)
    # bets = db.relationship('Bet', backref='match', lazy=True)

    def __repr__(self):
        return f"<Match {self.home_team} vs {self.away_team} @ {self.start_time}>"

    def to_dict(self):
        # Simple serialization, consider Marshmallow for complex cases
        return {
            'match_id': self.match_id,
            'external_match_id': self.external_match_id,
            'round_number': self.round.round_number, # Access via relationship
            'year': self.round.year, # Access via relationship
            'home_team': self.home_team,
            'away_team': self.away_team,
            'start_time': self.start_time.isoformat() if self.start_time else None,
            'home_odds': float(self.home_odds) if self.home_odds is not None else None,
            'away_odds': float(self.away_odds) if self.away_odds is not None else None,
            'status': self.status,
            'result_home_score': self.result_home_score,
            'result_away_score': self.result_away_score,
            'winner': self.winner,
            'last_odds_update': self.last_odds_update.isoformat() if self.last_odds_update else None
        }

# Add Bet and BankrollHistory models in later phases