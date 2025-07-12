# app/api/routes.py
"""
API Routes for NRL Tipping Application

Contains all REST API endpoints for user management, authentication, betting,
match data, AI predictions, and real-time updates. Handles both traditional
registration/login and OAuth integration with comprehensive betting functionality.
"""

# =============================================================================
# IMPORTS
# =============================================================================
from flask import request, redirect, url_for, session, current_app, Response, stream_with_context
from flask_restful import Resource, reqparse
from flask_jwt_extended import create_access_token, create_refresh_token, jwt_required, get_jwt_identity
from app.models import Round, Match, User, Bet, BankrollHistory, AIPrediction
from app import db, oauth
from datetime import datetime, timezone
from itsdangerous import URLSafeTimedSerializer, SignatureExpired, BadTimeSignature
import secrets
from urllib.parse import urlencode
import logging
from decimal import Decimal, InvalidOperation
from .settlement import settle_bets_for_match
from app.sse_events import sse_event_stream_generator
from app.services.betting_service import place_bet_for_user
from app.services.ai_prediction_service import AI_BOT_USERNAME

# =============================================================================
# UTILITY FUNCTIONS
# =============================================================================
def calculate_initial_bankroll():
    """
    Calculate initial bankroll based on current active round.
    Returns tuple of (initial_bankroll, round_number)
    """
    now = datetime.now(timezone.utc)
    current_year = now.year
    
    active_round = Round.query.filter_by(status='Active', year=current_year).first()
    
    if active_round:
        round_number = active_round.round_number
    else:
        upcoming_round = Round.query.filter_by(status='Upcoming', year=current_year) \
                                   .order_by(Round.start_date.asc()).first()
        if upcoming_round:
            round_number = upcoming_round.round_number
        else:
            round_number = 1
    
    initial_bankroll = Decimal(str(round_number * 1000))
    return initial_bankroll, round_number

# =============================================================================
# REQUEST PARSERS
# =============================================================================
_user_parser = reqparse.RequestParser()
_user_parser.add_argument('username', type=str, required=True, help='Username cannot be blank')
_user_parser.add_argument('email', type=str, required=True, help='Email cannot be blank')
_user_parser.add_argument('password', type=str, required=True, help='Password cannot be blank')

_login_parser = reqparse.RequestParser()
_login_parser.add_argument('username', type=str, required=True, help='Username cannot be blank')
_login_parser.add_argument('password', type=str, required=True, help='Password cannot be blank')

_bet_parser = reqparse.RequestParser()
_bet_parser.add_argument('match_id', type=int, required=True, help='Match ID cannot be blank')
_bet_parser.add_argument('team_selected', type=str, required=True, help='Team selection cannot be blank')
_bet_parser.add_argument('amount', type=str, required=True, help='Bet amount cannot be blank')

_result_parser = reqparse.RequestParser()
_result_parser.add_argument('home_score', type=int, required=True, help='Home score is required (integer)', location='json')
_result_parser.add_argument('away_score', type=int, required=True, help='Away score is required (integer)', location='json')

_pw_reset_request_parser = reqparse.RequestParser()
_pw_reset_request_parser.add_argument('email', type=str, required=True, help='Email cannot be blank')

_pw_reset_confirm_parser = reqparse.RequestParser()
_pw_reset_confirm_parser.add_argument('token', type=str, required=True, help='Token cannot be blank')
_pw_reset_confirm_parser.add_argument('new_password', type=str, required=True, help='New password cannot be blank')

class RoundListResource(Resource):
    def get(self):
        """Get list of all rounds"""
        rounds = Round.query.order_by(Round.year, Round.round_number).all()
        return {'rounds': [r.to_dict() for r in rounds]}, 200


class MatchListResource(Resource):
    def get(self):
        now = datetime.now(timezone.utc)
        target_round_number = request.args.get('round_number', type=int)
        target_year = request.args.get('year', type=int, default=now.year)

        query = Match.query.join(Round)
        current_active_round_obj = None

        if target_round_number is None:
            active_round = Round.query.filter_by(status='Active', year=target_year).first()
            if active_round:
                query = query.filter(Match.round_id == active_round.round_id)
                current_active_round_obj = active_round
            else:
                upcoming_round = Round.query.filter_by(status='Upcoming', year=target_year) \
                                         .order_by(Round.start_date.asc()).first()
                if upcoming_round:
                    query = query.filter(Match.round_id == upcoming_round.round_id)
                    current_active_round_obj = upcoming_round
                else:
                    return {'matches': [], 'round_info': None, 'message': f'No active or upcoming rounds found for {target_year}.'}, 200
        else:
            specific_round = Round.query.filter_by(round_number=target_round_number, year=target_year).first()
            if specific_round:
                query = query.filter(Match.round_id == specific_round.round_id)
                current_active_round_obj = specific_round
            else:
                return {'matches': [], 'round_info': None, 'message': f'Round {target_round_number} for year {target_year} not found.'}, 404

        matches = query.order_by(Match.start_time.asc()).all()

        round_info = None
        if current_active_round_obj:
            round_info = {
                'round_id': current_active_round_obj.round_id,
                'round_number': current_active_round_obj.round_number,
                'year': current_active_round_obj.year,
                'status': current_active_round_obj.status
            }

        return {'matches': [m.to_dict() for m in matches], 'round_info': round_info}, 200

class MatchResource(Resource):
     def get(self, match_id):
         """Get details for a specific match"""
         match = Match.query.get_or_404(match_id)
         return {'match': match.to_dict()}, 200

# =============================================================================
# AUTHENTICATION RESOURCES
# =============================================================================

class UserRegister(Resource):
    def post(self):
        data = _user_parser.parse_args()

        if User.find_by_username(data['username']):
            return {'message': 'A user with that username already exists'}, 400
        if User.find_by_email(data['email']):
            return {'message': 'A user with that email already exists'}, 400

        initial_bankroll, round_number = calculate_initial_bankroll()
        
        user = User(
            username=data['username'],
            email=data['email'].lower(),
            is_email_verified = False,
            bankroll = initial_bankroll
        )
        user.set_password(data['password'])

        try:
            db.session.add(user)
            db.session.flush()

            history_entry = BankrollHistory(
                user_id=user.user_id,
                round_number=round_number,
                change_type='Initial Deposit',
                related_bet_id=None,
                amount_change=initial_bankroll,
                previous_balance=Decimal('0.00'),
                new_balance=initial_bankroll,
                timestamp=datetime.now(timezone.utc)
            )
            db.session.add(history_entry)

            db.session.commit()
            print(f"User {user.username} created with ID {user.user_id}")
            print(f"Initial bankroll ${initial_bankroll} (Round {round_number} x $1000) logged for user {user.user_id}")

        except Exception as e:
            db.session.rollback()
            print(f"Error saving user or logging initial bankroll: {e}")
            import traceback
            traceback.print_exc()
            return {'message': 'An error occurred during registration.'}, 500

        serializer = URLSafeTimedSerializer(current_app.config['SECRET_KEY'])
        verification_token = serializer.dumps(user.email, salt='email-verification-salt')

        print(f"--- Email Verification Token for {user.email}: {verification_token} ---")

        try:
            user.save_to_db()
        except Exception as e:
            print(f"Error saving user: {e}")
            return {'message': 'An error occurred during registration.'}, 500

        return {
            'message': 'User created successfully. Please verify your email.',
            'verification_token_for_testing': verification_token
            }, 201

class UserLogin(Resource):
    def post(self):
        data = _login_parser.parse_args()
        user = User.find_by_username(data['username'])

        if user and user.check_password(data['password']):
            access_token = create_access_token(identity=str(user.user_id), fresh=True)
            refresh_token = create_refresh_token(identity=str(user.user_id))

            user.last_login = datetime.now(timezone.utc)
            db.session.commit()

            return {
                'access_token': access_token,
                'refresh_token': refresh_token
            }, 200

        return {'message': 'Invalid credentials'}, 401
    
class TokenRefresh(Resource):
    @jwt_required(refresh=True)
    def post(self):
        current_user_id = get_jwt_identity()
        new_access_token = create_access_token(identity=current_user_id, fresh=False)
        return {'access_token': new_access_token}, 200

# =============================================================================
# OAUTH AUTHENTICATION RESOURCES
# =============================================================================

class GoogleLogin(Resource):
    def get(self):
        redirect_uri = current_app.config.get('GOOGLE_REDIRECT_URI')
        if not redirect_uri:
            redirect_uri = url_for('googleauthcallback', _external=True)
        
        import time
        state = f"oauth_state_{int(time.time())}"
        return oauth.google.authorize_redirect(redirect_uri, state=state)

class GoogleAuthCallback(Resource):
    def get(self):
        try:
            token = oauth.google.authorize_access_token()
        except Exception as e:
             print(f"Error authorizing access token: {e}")
             frontend_error_url = f"{current_app.config.get('FRONTEND_URL', 'http://localhost:5173')}/login?error=google_auth_failed"
             return redirect(frontend_error_url)

        user_info = oauth.google.get('https://openidconnect.googleapis.com/v1/userinfo').json()
        google_id = user_info.get('sub')
        email = user_info.get('email')
        username = user_info.get('email').split('@')[0]

        if not email or not google_id:
             frontend_error_url = f"{current_app.config.get('FRONTEND_URL', 'http://localhost:5173')}/login?error=google_info_missing"
             return redirect(frontend_error_url)

        user = User.find_by_google_id(google_id)
        is_new_user = False
        if not user:
            user = User.find_by_email(email)
            if user:
                if user.google_id is None: user.google_id = google_id
                if not user.is_email_verified: user.is_email_verified = True
            else:
                is_new_user = True
                initial_bankroll, round_number = calculate_initial_bankroll()
                
                temp_username = username
                counter = 1
                while User.find_by_username(temp_username):
                    temp_username = f"{username}{counter}"
                    counter += 1
                username = temp_username
                user = User(username=username, email=email.lower(), google_id=google_id, is_email_verified=True, bankroll=initial_bankroll)
            try:
                 db.session.add(user)
                 db.session.flush()
                 
                 if is_new_user:
                     history_entry = BankrollHistory(
                         user_id=user.user_id,
                         round_number=round_number,
                         change_type='Initial Deposit',
                         related_bet_id=None,
                         amount_change=initial_bankroll,
                         previous_balance=Decimal('0.00'),
                         new_balance=initial_bankroll,
                         timestamp=datetime.now(timezone.utc)
                     )
                     db.session.add(history_entry)
                     print(f"Google user {user.username} created with initial bankroll ${initial_bankroll} (Round {round_number} x $1000)")
                 
                 db.session.commit()
            except Exception as e:
                 db.session.rollback()
                 print(f"Error saving Google user: {e}")
                 frontend_error_url = f"{current_app.config.get('FRONTEND_URL', 'http://localhost:5173')}/login?error=google_db_error"
                 return redirect(frontend_error_url)

        access_token = create_access_token(identity=str(user.user_id), fresh=True)
        refresh_token = create_refresh_token(identity=str(user.user_id))

        user.last_login = datetime.now(timezone.utc)
        db.session.commit()

        frontend_base_url = current_app.config.get('FRONTEND_URL', 'http://localhost:5173')
        frontend_callback_path = '/auth/google/callback'
        params = {
            'access_token': access_token,
            'refresh_token': refresh_token
        }
        redirect_url = f"{frontend_base_url}{frontend_callback_path}?{urlencode(params)}"

        print(f"Redirecting to frontend: {redirect_url}")
        return redirect(redirect_url, code=302)

# =============================================================================
# PASSWORD RESET AND EMAIL VERIFICATION RESOURCES
# =============================================================================

class RequestPasswordReset(Resource):
    def post(self):
        data = _pw_reset_request_parser.parse_args()
        user = User.find_by_email(data['email'])

        if not user:
            return {'message': 'If an account with that email exists, a reset token has been generated.'}, 200

        serializer = URLSafeTimedSerializer(current_app.config['SECRET_KEY'])
        token = serializer.dumps(user.email, salt='password-reset-salt')

        print(f"--- Password Reset Token for {user.email}: {token} ---")

        return {
            'message': 'If an account with that email exists, a reset token has been generated.',
            'reset_token_for_testing': token
            }, 200

class ResetPassword(Resource):
    def post(self):
        data = _pw_reset_confirm_parser.parse_args()
        serializer = URLSafeTimedSerializer(current_app.config['SECRET_KEY'])

        try:
            email = serializer.loads(
                data['token'],
                salt='password-reset-salt',
                max_age=3600
            )
        except SignatureExpired:
            return {'message': 'Password reset token has expired.'}, 400
        except BadTimeSignature:
             return {'message': 'Invalid password reset token.'}, 400
        except Exception as e:
            print(f"Token verification error: {e}")
            return {'message': 'Invalid password reset token.'}, 400

        user = User.find_by_email(email)
        if not user:
            return {'message': 'User not found.'}, 404

        user.set_password(data['new_password'])
        try:
            user.save_to_db()
        except Exception as e:
            print(f"Error saving new password: {e}")
            return {'message': 'An error occurred setting the new password.'}, 500

        return {'message': 'Password has been reset successfully.'}, 200

class VerifyEmail(Resource):
    def get(self, token):
        serializer = URLSafeTimedSerializer(current_app.config['SECRET_KEY'])
        try:
             email = serializer.loads(
                token,
                salt='email-verification-salt',
                max_age=86400
             )
        except SignatureExpired:
            return {'message': 'Email verification link has expired.'}, 400
        except BadTimeSignature:
             return {'message': 'Invalid email verification link.'}, 400
        except Exception as e:
            print(f"Token verification error: {e}")
            return {'message': 'Invalid email verification link.'}, 400

        user = User.find_by_email(email)
        if not user:
             return {'message': 'User not found.'}, 404

        if user.is_email_verified:
             return {'message': 'Email is already verified.'}, 200

        user.is_email_verified = True
        try:
            user.save_to_db()
        except Exception as e:
            print(f"Error marking email as verified: {e}")
            return {'message': 'An error occurred during email verification.'}, 500

        return {'message': 'Email verified successfully!'}, 200

# =============================================================================
# USER PROFILE AND PROTECTED RESOURCES
# =============================================================================

class UserProfile(Resource):
    @jwt_required()
    def get(self):
        current_user_id_str = get_jwt_identity()
        user = User.query.get(int(current_user_id_str))

        if not user:
            return {"message": "User not found"}, 404

        return {
            "user_id": user.user_id,
            "username": user.username,
            "email": user.email,
            "bankroll": float(user.bankroll),
            "message": "Protected data access successful"
         }, 200

# =============================================================================
# BETTING RESOURCES
# =============================================================================

class PlaceBet(Resource):
    @jwt_required()
    def post(self):
        data = _bet_parser.parse_args()
        user = User.query.get(int(get_jwt_identity()))
        match = Match.query.get(data['match_id'])
        bet_amount = Decimal(data['amount'])

        success, result = place_bet_for_user(
            user=user,
            match=match,
            team_selected=data['team_selected'],
            bet_amount=bet_amount
        )

        if success:
            return {'message': 'Bet placed successfully!', 'bet_details': result.to_dict()}, 201
        else:
            return {'message': result}, 400

class UserBetList(Resource):
    @jwt_required()
    def get(self):
        current_user_id = int(get_jwt_identity())
        user = User.query.get(current_user_id)
        if not user:
            return {'message': 'User not found'}, 404

        status_filter = request.args.get('status')
        query = user.bets

        if status_filter:
            allowed_statuses = ['Pending', 'Active', 'Won', 'Lost', 'Void', 'Settled']
            if status_filter == 'Settled':
                 query = query.filter(Bet.status.in_(['Won', 'Lost', 'Void']))
            elif status_filter in allowed_statuses:
                 query = query.filter(Bet.status == status_filter)

        bets = query.order_by(Bet.placement_time.desc()).all()
        return {'bets': [bet.to_dict() for bet in bets]}, 200

class UserBankrollHistoryList(Resource):
     @jwt_required()
     def get(self):
        current_user_id = int(get_jwt_identity())
        user = User.query.get(current_user_id)
        if not user:
            return {'message': 'User not found'}, 404

        history_items = user.bankroll_history.order_by(BankrollHistory.timestamp.desc()).all()

        return {
             'bankroll_history': [item.to_dict() for item in history_items]
        }, 200

# =============================================================================
# AI BOT RESOURCES
# =============================================================================

class AIBotBetList(Resource):
    def get(self):
        """Get all bets placed by the AI bot"""
        ai_bot = User.query.filter_by(username=AI_BOT_USERNAME).first()
        if not ai_bot:
            return {'message': f'AI Bot user "{AI_BOT_USERNAME}" not found.'}, 404

        status_filter = request.args.get('status')
        query = ai_bot.bets

        if status_filter:
            allowed_statuses = ['Pending', 'Active', 'Won', 'Lost', 'Void', 'Settled']
            if status_filter == 'Settled':
                query = query.filter(Bet.status.in_(['Won', 'Lost', 'Void']))
            elif status_filter in allowed_statuses:
                query = query.filter(Bet.status == status_filter)

        bets = query.order_by(Bet.placement_time.desc()).all()

        return {
            'ai_bot_username': AI_BOT_USERNAME,
            'ai_bot_user_id': ai_bot.user_id,
            'total_bets': len(bets),
            'bets': [bet.to_dict() for bet in bets]
        }, 200

class AIBotBankrollHistory(Resource):
    def get(self):
        """Get bankroll history for the AI bot"""
        ai_bot = User.query.filter_by(username=AI_BOT_USERNAME).first()
        if not ai_bot:
            return {'message': f'AI Bot user "{AI_BOT_USERNAME}" not found.'}, 404

        history_items = ai_bot.bankroll_history.order_by(BankrollHistory.timestamp.desc()).all()

        return {
            'ai_bot_username': AI_BOT_USERNAME,
            'ai_bot_user_id': ai_bot.user_id,
            'current_bankroll': float(ai_bot.bankroll),
            'total_history_entries': len(history_items),
            'bankroll_history': [item.to_dict() for item in history_items]
        }, 200

class AIPredictionsByRound(Resource):
    def get(self, year, round_number):
        """Get AI predictions for a specific round"""
        logger = logging.getLogger(__name__)
        logger.info(f"Fetching AI predictions for Year {year}, Round {round_number}")
        
        round_obj = Round.query.filter_by(year=year, round_number=round_number).first()
        if not round_obj:
            logger.warning(f"Round not found for Year {year}, Round {round_number}")
            return {'message': 'Round not found.'}, 404

        matches_in_round = round_obj.matches.all()
        match_ids_in_round = [m.match_id for m in matches_in_round]
        logger.info(f"Found {len(match_ids_in_round)} matches in round: {match_ids_in_round}")

        if not match_ids_in_round:
            logger.info("No matches found in round")
            return {'predictions': {}}, 200
        
        ai_bot = User.query.filter_by(username=AI_BOT_USERNAME).first()
        if not ai_bot:
            logger.error(f'AI Bot user "{AI_BOT_USERNAME}" not found in database')
            return {'message': f'AI Bot user "{AI_BOT_USERNAME}" not found.'}, 404
        
        logger.info(f"Found AI bot user: {ai_bot.username} (ID: {ai_bot.user_id})")

        predictions = AIPrediction.query.filter(
            AIPrediction.user_id == ai_bot.user_id,
            AIPrediction.match_id.in_(match_ids_in_round)
        ).all()
        
        logger.info(f"Found {len(predictions)} AI predictions for the round")
        if predictions:
            logger.info(f"Sample prediction: Match {predictions[0].match_id} - {predictions[0].home_team} vs {predictions[0].away_team}")

        predictions_by_match_id = {
            p.match_id: {
                'prediction_id': p.prediction_id,
                'home_win_probability': float(p.home_win_probability),
                'away_win_probability': float(p.away_win_probability),
                'predicted_winner': p.predicted_winner,
                'model_confidence': float(p.model_confidence),
                'betting_recommendation': p.betting_recommendation,
                'recommended_team': p.recommended_team,
                'confidence_level': p.confidence_level,
                'kelly_criterion_stake': float(p.kelly_criterion_stake),
                'created_at': p.created_at.isoformat()
            } for p in predictions
        }
        
        logger.info(f"Returning {len(predictions_by_match_id)} predictions to frontend")
        return {'predictions': predictions_by_match_id}, 200

class AIPredictionsByRoundRange(Resource):
    def get(self, year, start_round, end_round):
        """Get AI predictions for a range of rounds"""
        logger = logging.getLogger(__name__)
        logger.info(f"Fetching AI predictions for Year {year}, Rounds {start_round}-{end_round}")
        
        # Validate round range
        if start_round > end_round:
            return {'message': 'Start round cannot be greater than end round.'}, 400
        
        if end_round - start_round > 20:  # Prevent excessive queries
            return {'message': 'Round range too large. Maximum 20 rounds per request.'}, 400
        
        # Get all rounds in the specified range
        rounds_in_range = Round.query.filter(
            Round.year == year,
            Round.round_number >= start_round,
            Round.round_number <= end_round
        ).all()
        
        if not rounds_in_range:
            logger.warning(f"No rounds found for Year {year}, Rounds {start_round}-{end_round}")
            return {'message': 'No rounds found in the specified range.'}, 404
        
        logger.info(f"Found {len(rounds_in_range)} rounds in range")
        
        # Get all matches from these rounds
        round_ids = [r.round_id for r in rounds_in_range]
        matches_in_range = Match.query.filter(Match.round_id.in_(round_ids)).all()
        match_ids_in_range = [m.match_id for m in matches_in_range]
        
        logger.info(f"Found {len(match_ids_in_range)} matches across {len(rounds_in_range)} rounds")
        
        if not match_ids_in_range:
            logger.info("No matches found in round range")
            return {'predictions': {}}, 200
        
        # Get AI bot user
        ai_bot = User.query.filter_by(username=AI_BOT_USERNAME).first()
        if not ai_bot:
            logger.error(f'AI Bot user "{AI_BOT_USERNAME}" not found in database')
            return {'message': f'AI Bot user "{AI_BOT_USERNAME}" not found.'}, 404
        
        logger.info(f"Found AI bot user: {ai_bot.username} (ID: {ai_bot.user_id})")
        
        # Get all predictions for matches in the round range
        predictions = AIPrediction.query.filter(
            AIPrediction.user_id == ai_bot.user_id,
            AIPrediction.match_id.in_(match_ids_in_range)
        ).all()
        
        logger.info(f"Found {len(predictions)} AI predictions across the round range")
        
        # Organize predictions by round and match
        predictions_by_round = {}
        
        # Create a mapping of match_id to round_number for efficient lookup
        match_to_round = {}
        for match in matches_in_range:
            round_obj = next((r for r in rounds_in_range if r.round_id == match.round_id), None)
            if round_obj:
                match_to_round[match.match_id] = round_obj.round_number
        
        # Group predictions by round
        for p in predictions:
            round_number = match_to_round.get(p.match_id)
            if round_number is not None:
                if round_number not in predictions_by_round:
                    predictions_by_round[round_number] = {}
                
                predictions_by_round[round_number][p.match_id] = {
                    'prediction_id': p.prediction_id,
                    'home_win_probability': float(p.home_win_probability),
                    'away_win_probability': float(p.away_win_probability),
                    'predicted_winner': p.predicted_winner,
                    'model_confidence': float(p.model_confidence),
                    'betting_recommendation': p.betting_recommendation,
                    'recommended_team': p.recommended_team,
                    'confidence_level': p.confidence_level,
                    'kelly_criterion_stake': float(p.kelly_criterion_stake),
                    'created_at': p.created_at.isoformat()
                }
        
        # Also include round information for context
        round_info = {}
        for round_obj in rounds_in_range:
            round_info[round_obj.round_number] = {
                'round_id': round_obj.round_id,
                'round_number': round_obj.round_number,
                'year': round_obj.year,
                'status': round_obj.status,
                'start_date': round_obj.start_date.isoformat() if round_obj.start_date else None,
                'end_date': round_obj.end_date.isoformat() if round_obj.end_date else None
            }
        
        total_predictions = sum(len(round_preds) for round_preds in predictions_by_round.values())
        logger.info(f"Returning {total_predictions} predictions across {len(predictions_by_round)} rounds to frontend")
        
        return {
            'predictions': predictions_by_round,
            'round_info': round_info,
            'summary': {
                'year': year,
                'start_round': start_round,
                'end_round': end_round,
                'rounds_found': len(rounds_in_range),
                'total_matches': len(match_ids_in_range),
                'total_predictions': total_predictions
            }
        }, 200

# =============================================================================
# LEADERBOARD RESOURCES
# =============================================================================

class GlobalLeaderboard(Resource):
    def get(self):
        """Get global user leaderboard ranked by bankroll"""
        page = request.args.get('page', 1, type=int)
        limit = request.args.get('limit', 50, type=int)
        limit = min(limit, 200)

        try:
            leaderboard_page = User.query.filter(User.active == True) \
                                         .order_by(User.bankroll.desc(), User.username.asc()) \
                                         .paginate(page=page, per_page=limit, error_out=False)

            users_data = [{
                'rank': (leaderboard_page.page - 1) * leaderboard_page.per_page + i + 1,
                'user_id': user.user_id,
                'username': user.username,
                'bankroll': float(user.bankroll)
             } for i, user in enumerate(leaderboard_page.items)]

            return {
                'leaderboard': users_data,
                'total_users': leaderboard_page.total,
                'current_page': leaderboard_page.page,
                'total_pages': leaderboard_page.pages,
                'per_page': leaderboard_page.per_page
             }, 200

        except Exception as e:
             print(f"Error fetching global leaderboard: {e}")
             import traceback
             traceback.print_exc()
             return {'message': 'Error retrieving leaderboard data.'}, 500

# =============================================================================
# ROUTE INITIALIZATION
# =============================================================================

def initialize_routes(app, api):
    """Initialize all API routes and endpoints"""
    
    # Match and Round endpoints
    api.add_resource(RoundListResource, '/api/rounds')
    api.add_resource(MatchListResource, '/api/matches', '/api/matches/upcoming')
    api.add_resource(MatchResource, '/api/matches/<int:match_id>')
    
    # Authentication endpoints
    api.add_resource(UserRegister, '/api/auth/register')
    api.add_resource(UserLogin, '/api/auth/login')
    api.add_resource(TokenRefresh, '/api/auth/refresh')
    api.add_resource(GoogleLogin, '/api/auth/google/login')
    api.add_resource(GoogleAuthCallback, '/api/auth/google/callback', endpoint='googleauthcallback')
    api.add_resource(RequestPasswordReset, '/api/auth/request-password-reset')
    api.add_resource(ResetPassword, '/api/auth/reset-password')
    api.add_resource(VerifyEmail, '/api/auth/verify-email/<string:token>', endpoint='verifyemail')
    
    # User profile and data endpoints
    api.add_resource(UserProfile, '/api/user/profile')
    api.add_resource(UserBankrollHistoryList, '/api/user/bankroll-history')
    
    # Betting endpoints
    api.add_resource(PlaceBet, '/api/bets/place')
    api.add_resource(UserBetList, '/api/bets')
    
    # AI Bot endpoints
    api.add_resource(AIBotBetList, '/api/ai-bot/bets')
    api.add_resource(AIBotBankrollHistory, '/api/ai-bot/bankroll-history')
    api.add_resource(AIPredictionsByRound, '/api/ai-predictions/year/<int:year>/round/<int:round_number>')
    api.add_resource(AIPredictionsByRoundRange, '/api/ai-predictions/year/<int:year>/rounds/<int:start_round>-<int:end_round>')

    # Leaderboard endpoints
    api.add_resource(GlobalLeaderboard, '/api/leaderboard/global')

    # Server-Sent Events endpoint
    @app.route('/api/stream/updates')
    def sse_stream():
        return Response(stream_with_context(sse_event_stream_generator()), mimetype='text/event-stream')

    print("---API AND SSE ROUTES INITIALISED---")

