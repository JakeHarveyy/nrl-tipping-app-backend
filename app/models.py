
"""
Database Models for NRL Tipping Application Backend

This file defines the SQLAlchemy ORM models that represent the core data structures
for the NRL tipping application. It includes models for:

- User: User accounts with authentication, bankroll management, and Google OAuth support
- Round: NRL competition rounds with season/year tracking
- Match: Individual NRL matches with teams, odds, venues, and results
- Bet: User betting records with amounts, odds, and settlement tracking
- BankrollHistory: Audit trail of all bankroll changes and transactions
- AIPrediction: AI model predictions and betting recommendations for matches

These models support the application's core features including user management,
match data storage, betting functionality, financial tracking, and AI-powered
predictions for NRL matches.
"""

from datetime import datetime, timezone
from decimal import Decimal
from app import db, bcrypt

class User(db.Model):
    __tablename__ = 'users'
    user_id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False, index=True)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=True) # Nullable for local OAuth
    google_id = db.Column(db.String(255), unique=True, nullable=True, index=True) #for google auth
    bankroll = db.Column(db.Numeric(12, 2), nullable=False, default=Decimal('1000.00'))
    is_email_verified = db.Column(db.Boolean, default=False)
    is_bot = db.Column(db.Boolean, default=False)
    registration_date = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)) # Use timezone.utc
    last_login = db.Column(db.DateTime(timezone=True), nullable=True)
    active = db.Column(db.Boolean, default=True)
    
    bets = db.relationship('Bet', backref='user', lazy='dynamic')
    bankroll_history = db.relationship('BankrollHistory', backref='user', lazy='dynamic')

    def __repr__(self):
        return f"<User {self.username}>"

    def set_password(self, password):
        """Hashes the password and stores it"""
        self.password_hash = bcrypt.generate_password_hash(password).decode('utf-8')

    def check_password(self, password):
        """Checks if the provided password matches the stored hash"""
        if not self.password_hash: 
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

    matches = db.relationship('Match', backref='round', lazy='dynamic')

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
    venue = db.Column(db.String(255), nullable=True)  # Stadium/venue name
    venue_city = db.Column(db.String(100), nullable=True)  # City where venue is located
    status = db.Column(db.String(20), nullable=False, default='Scheduled', index=True) # Scheduled, Live, Completed, Postponed, Cancelled
    result_home_score = db.Column(db.Integer, nullable=True)
    result_away_score = db.Column(db.Integer, nullable=True)
    winner = db.Column(db.String(100), nullable=True) # Home Team Name, Away Team Name, or 'Draw'
    last_odds_update = db.Column(db.DateTime(timezone=True), nullable=True)
    bets = db.relationship('Bet', backref='match', lazy='dynamic')

    def __repr__(self):
        return f"<Match {self.home_team} vs {self.away_team} @ {self.start_time}>"

    def to_dict(self):
        return {
            'match_id': self.match_id,
            'external_match_id': self.external_match_id,
            'round_number': self.round.round_number, 
            'year': self.round.year,
            'home_team': self.home_team,
            'away_team': self.away_team,
            'start_time': self.start_time.isoformat() if self.start_time else None,
            'home_odds': float(self.home_odds) if self.home_odds is not None else None,
            'away_odds': float(self.away_odds) if self.away_odds is not None else None,
            'venue': self.venue,
            'venue_city': self.venue_city,
            'status': self.status,
            'result_home_score': self.result_home_score,
            'result_away_score': self.result_away_score,
            'winner': self.winner,
            'last_odds_update': self.last_odds_update.isoformat() if self.last_odds_update else None
        }

class Bet(db.Model):
    __tablename__ = 'bets'
    bet_id = db.Column(db.Integer, primary_key=True)
    # Foreign Keys linking to User and Match tables
    user_id = db.Column(db.Integer, db.ForeignKey('users.user_id'), nullable=False, index=True)
    match_id = db.Column(db.Integer, db.ForeignKey('matches.match_id'), nullable=False, index=True)
    round_id = db.Column(db.Integer, db.ForeignKey('rounds.round_id'), nullable=False, index=True)
    team_selected = db.Column(db.String(100), nullable=False) # e.g., "Broncos" or "Cowboys"
    amount = db.Column(db.Numeric(10, 2), nullable=False)
    odds_at_placement = db.Column(db.Numeric(6, 3), nullable=False)
    potential_payout = db.Column(db.Numeric(12, 2), nullable=False) # amount * odds_at_placement
    status = db.Column(db.String(20), nullable=False, default='Pending', index=True) # Pending, Active, Won, Lost, Void
    placement_time = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    settlement_time = db.Column(db.DateTime(timezone=True), nullable=True)

    def __repr__(self):
        return f"<Bet {self.bet_id} User:{self.user_id} Match:{self.match_id} Amt:{self.amount} Status:{self.status}>"

    def to_dict(self):
        # Include related info for easy frontend display
        return {
            'bet_id': self.bet_id,
            'user_id': self.user_id,
            'match_id': self.match_id,
            'round_number': self.match.round.round_number, 
            'home_team': self.match.home_team,
            'away_team': self.match.away_team,
            'match_start_time': self.match.start_time.isoformat(),
            'team_selected': self.team_selected,
            'amount': float(self.amount),
            'odds_at_placement': float(self.odds_at_placement),
            'potential_payout': float(self.potential_payout),
            'status': self.status,
            'placement_time': self.placement_time.isoformat(),
            'settlement_time': self.settlement_time.isoformat() if self.settlement_time else None,
        }
    
class BankrollHistory(db.Model):
    __tablename__ = 'bankroll_history'
    history_id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.user_id'), nullable=False, index=True)
    round_number = db.Column(db.Integer, nullable=True) 
    change_type = db.Column(db.String(50), nullable=False, index=True)  # Examples: 'Initial Deposit', 'Weekly Addition', 'Bet Placement', 'Bet Win', 'Bet Loss', 'Bet Void', 'Admin Adjustment'
    related_bet_id = db.Column(db.Integer, db.ForeignKey('bets.bet_id'), nullable=True, index=True)
    related_bet = db.relationship('Bet', backref='bankroll_history_entries') 
    amount_change = db.Column(db.Numeric(12, 2), nullable=False) # Positive or negative
    previous_balance = db.Column(db.Numeric(12, 2), nullable=False)
    new_balance = db.Column(db.Numeric(12, 2), nullable=False)
    timestamp = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True)

    def __repr__(self):
        return f"<BankrollHistory {self.history_id} User:{self.user_id} Type:{self.change_type} Amt:{self.amount_change}>"

    def to_dict(self):
         return {
            'history_id': self.history_id,
            'user_id': self.user_id,
            'round_number': self.round_number,
            'change_type': self.change_type,
            'related_bet_id': self.related_bet_id,
            'amount_change': float(self.amount_change),
            'previous_balance': float(self.previous_balance),
            'new_balance': float(self.new_balance),
            'timestamp': self.timestamp.isoformat() if self.timestamp else None
        }
    
class AIPrediction(db.Model):
    __tablename__ = 'ai_predictions'
    prediction_id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.user_id'), nullable=False, index=True)
    match_id = db.Column(db.Integer, db.ForeignKey('matches.match_id'), nullable=False, index=True)
    home_team = db.Column(db.String(100), nullable=False)
    away_team = db.Column(db.String(100), nullable=False)
    match_date = db.Column(db.DateTime(timezone=True), nullable=False)
    # AI Model predictions
    home_win_probability = db.Column(db.Numeric(5, 4), nullable=False)  # 0.0000 to 1.0000
    away_win_probability = db.Column(db.Numeric(5, 4), nullable=False)  # 0.0000 to 1.0000
    predicted_winner = db.Column(db.String(100), nullable=False)
    model_confidence = db.Column(db.Numeric(5, 4), nullable=False)  # 0.0000 to 1.0000
    # Betting recommendation
    betting_recommendation = db.Column(db.String(100), nullable=False)  # "Bet Team" or "No Bet"
    recommended_team = db.Column(db.String(100), nullable=True)  # Team to bet on if recommended
    confidence_level = db.Column(db.String(20), nullable=False)  # "High", "Medium", "Low"
    kelly_criterion_stake = db.Column(db.Numeric(5, 4), nullable=False)  # Recommended stake percentage
    # Metadata
    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)) 
    # Relationships
    user = db.relationship('User', backref='ai_predictions')
    match = db.relationship('Match', backref='ai_predictions')

    def __repr__(self):
        return f"<AIPrediction {self.prediction_id} Match:{self.match_id} Winner:{self.predicted_winner}>"