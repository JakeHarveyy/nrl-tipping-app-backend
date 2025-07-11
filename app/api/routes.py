# app/api/routes.py
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

class RoundListResource(Resource): # Ensure it inherits from Resource
    def get(self):
        """Get list of all rounds"""
        # ... (implementation as before) ...
        rounds = Round.query.order_by(Round.year, Round.round_number).all()
        return {'rounds': [r.to_dict() for r in rounds]}, 200


class MatchListResource(Resource):
    def get(self):
        now = datetime.now(timezone.utc)
        target_round_number = request.args.get('round_number', type=int)
        target_year = request.args.get('year', type=int, default=now.year)
        # status_filter = request.args.get('status', 'Scheduled') # We might not need status filter if filtering by round status

        query = Match.query.join(Round) # Always join with Round

        current_active_round_obj = None

        if target_round_number is None:
            # No specific round requested, try to find the 'Active' round
            active_round = Round.query.filter_by(status='Active', year=target_year).first()
            if active_round:
                query = query.filter(Match.round_id == active_round.round_id)
                current_active_round_obj = active_round
            else:
                # No active round, find the earliest 'Upcoming' round for the year
                upcoming_round = Round.query.filter_by(status='Upcoming', year=target_year) \
                                         .order_by(Round.start_date.asc()).first()
                if upcoming_round:
                    query = query.filter(Match.round_id == upcoming_round.round_id)
                    current_active_round_obj = upcoming_round
                else:
                    # No active or upcoming rounds found for the year
                    return {'matches': [], 'round_info': None, 'message': f'No active or upcoming rounds found for {target_year}.'}, 200
        else:
            # Specific round requested
            specific_round = Round.query.filter_by(round_number=target_round_number, year=target_year).first()
            if specific_round:
                query = query.filter(Match.round_id == specific_round.round_id)
                current_active_round_obj = specific_round # Keep track of the round being displayed
            else:
                return {'matches': [], 'round_info': None, 'message': f'Round {target_round_number} for year {target_year} not found.'}, 404

        matches = query.order_by(Match.start_time.asc()).all()

        round_info = None
        if current_active_round_obj: # If a round was determined
            round_info = {
                'round_id': current_active_round_obj.round_id,
                'round_number': current_active_round_obj.round_number,
                'year': current_active_round_obj.year,
                'status': current_active_round_obj.status # <<< SEND ROUND STATUS
            }

        return {'matches': [m.to_dict() for m in matches], 'round_info': round_info}, 200

class MatchResource(Resource): # Ensure it inherits from Resource
     def get(self, match_id):
         """Get details for a specific match"""
         match = Match.query.get_or_404(match_id)
         return {'match': match.to_dict()}, 200

# --- Argument Parsers (using reqparse for simplicity now) ---
# Consider Marshmallow or Pydantic for more robust validation later

_user_parser = reqparse.RequestParser()
_user_parser.add_argument('username', type=str, required=True, help='Username cannot be blank')
_user_parser.add_argument('email', type=str, required=True, help='Email cannot be blank')
_user_parser.add_argument('password', type=str, required=True, help='Password cannot be blank')

_login_parser = reqparse.RequestParser()
_login_parser.add_argument('username', type=str, required=True, help='Username cannot be blank')
_login_parser.add_argument('password', type=str, required=True, help='Password cannot be blank')

# --- Parser for Placing Bets ---
_bet_parser = reqparse.RequestParser()
_bet_parser.add_argument('match_id', type=int, required=True, help='Match ID cannot be blank')
_bet_parser.add_argument('team_selected', type=str, required=True, help='Team selection cannot be blank')
_bet_parser.add_argument('amount', type=str, required=True, help='Bet amount cannot be blank') # Parse as string initially for Decimal conversion

# --- Parser for Simulating Results ---
_result_parser = reqparse.RequestParser()
# Use location='json' to strictly look in the JSON body
_result_parser.add_argument('home_score', type=int, required=True, help='Home score is required (integer)', location='json')
_result_parser.add_argument('away_score', type=int, required=True, help='Away score is required (integer)', location='json')

# --- Authentication Resources ---

class UserRegister(Resource):
    def post(self):
        data = _user_parser.parse_args()

        # Check if username or email already exists
        if User.find_by_username(data['username']):
            return {'message': 'A user with that username already exists'}, 400
        if User.find_by_email(data['email']):
            return {'message': 'A user with that email already exists'}, 400

        initial_bankroll = Decimal('1000.00')
        user = User(
            username=data['username'],
            email=data['email'].lower(), # Store email in lowercase
            is_email_verified = False,
            bankroll = initial_bankroll
        )
        user.set_password(data['password'])
        # user.is_email_verified = False # Add email verification later

        try:
            db.session.add(user) # Add user to session
            db.session.flush() # Flush to assign user.user_id without committing yet

            # --- Log Initial Bankroll ---
            history_entry = BankrollHistory(
                user_id=user.user_id,
                round_number=None, # Or determine current round if applicable/desired
                change_type='Initial Deposit',
                related_bet_id=None,
                amount_change=initial_bankroll,
                previous_balance=Decimal('0.00'), # Starting from zero
                new_balance=initial_bankroll,
                timestamp=datetime.now(timezone.utc) # Ensure timestamp matches
            )
            db.session.add(history_entry)
            # ---------------------------

            db.session.commit() # Commit both user and history entry together
            print(f"User {user.username} created with ID {user.user_id}")
            print(f"Initial bankroll history logged for user {user.user_id}")

        except Exception as e:
            db.session.rollback() # Rollback ALL changes if any error occurs
            print(f"Error saving user or logging initial bankroll: {e}")
            # Log the detailed exception
            import traceback
            traceback.print_exc()
            return {'message': 'An error occurred during registration.'}, 500


        # Generate verification token BEFORE saving (though order doesn't strictly matter here)
        serializer = URLSafeTimedSerializer(current_app.config['SECRET_KEY'])
        verification_token = serializer.dumps(user.email, salt='email-verification-salt')

        # TODO: Send email with verification_token in a real app
        # verify_url = url_for('verifyemail', token=verification_token, _external=True) # 'verifyemail' is endpoint name
        # send_email(user.email, "Verify Your Email", f"Click here: {verify_url}")
        print(f"--- Email Verification Token for {user.email}: {verification_token} ---") # For testing

        try:
            user.save_to_db()
        except Exception as e:
            print(f"Error saving user: {e}") # Log the error
            return {'message': 'An error occurred during registration.'}, 500

        # Return token FOR TESTING ONLY. Do not do this in production.
        return {
            'message': 'User created successfully. Please verify your email.',
            'verification_token_for_testing': verification_token # REMOVE IN PRODUCTION
            }, 201

class UserLogin(Resource):
    def post(self):
        data = _login_parser.parse_args()
        user = User.find_by_username(data['username'])

        if user and user.check_password(data['password']):
            # Credentials valid, create tokens
            # 'identity' can be user ID or any unique identifier
            access_token = create_access_token(identity=str(user.user_id), fresh=True)
            refresh_token = create_refresh_token(identity=str(user.user_id))

            # Update last_login time
            user.last_login = datetime.now(timezone.utc)
            db.session.commit() # No need for user.save_to_db() if just updating existing

            return {
                'access_token': access_token,
                'refresh_token': refresh_token
            }, 200

        return {'message': 'Invalid credentials'}, 401
    
class TokenRefresh(Resource):
    @jwt_required(refresh=True) # Requires a valid refresh token
    def post(self):
        current_user_id = get_jwt_identity()
        new_access_token = create_access_token(identity=current_user_id, fresh=False) # Mark as not fresh
        return {'access_token': new_access_token}, 200

# --- Google OAuth Routes ---

class GoogleLogin(Resource):
    def get(self):
        # Use the redirect URI from environment config to ensure it matches Google Cloud Console
        redirect_uri = current_app.config.get('GOOGLE_REDIRECT_URI')
        if not redirect_uri:
            # Fallback to constructing it dynamically if not set in config
            redirect_uri = url_for('googleauthcallback', _external=True)
        
        print(f"Redirect URI for Google: {redirect_uri}") # Debug print
        return oauth.google.authorize_redirect(redirect_uri)

class GoogleAuthCallback(Resource):
    def get(self):
        try:
            token = oauth.google.authorize_access_token()
        except Exception as e:
             print(f"Error authorizing access token: {e}")
             # Redirect to frontend login page with error query param
             frontend_error_url = f"{current_app.config.get('FRONTEND_URL', 'http://localhost:5173')}/login?error=google_auth_failed"
             return redirect(frontend_error_url)

        user_info = oauth.google.get('https://openidconnect.googleapis.com/v1/userinfo').json()
        google_id = user_info.get('sub')
        email = user_info.get('email')
        username = user_info.get('email').split('@')[0] # Default username

        if not email or not google_id:
             frontend_error_url = f"{current_app.config.get('FRONTEND_URL', 'http://localhost:5173')}/login?error=google_info_missing"
             return redirect(frontend_error_url)

        user = User.find_by_google_id(google_id)
        # ... (logic to find by email or create new user as before) ...
        if not user:
            user = User.find_by_email(email)
            if user:
                if user.google_id is None: user.google_id = google_id
                if not user.is_email_verified: user.is_email_verified = True
            else:
                temp_username = username
                counter = 1
                while User.find_by_username(temp_username):
                    temp_username = f"{username}{counter}"
                    counter += 1
                username = temp_username
                user = User(username=username, email=email.lower(), google_id=google_id, is_email_verified=True)
            try:
                 user.save_to_db()
            except Exception as e:
                 print(f"Error saving Google user: {e}")
                 frontend_error_url = f"{current_app.config.get('FRONTEND_URL', 'http://localhost:5173')}/login?error=google_db_error"
                 return redirect(frontend_error_url)

        # --- Create Tokens (ensure identity is string) ---
        access_token = create_access_token(identity=str(user.user_id), fresh=True)
        refresh_token = create_refresh_token(identity=str(user.user_id))
        # --------------------------------------------------

        user.last_login = datetime.now(timezone.utc)
        db.session.commit()

        # --- Redirect to Frontend with Tokens ---
        frontend_base_url = current_app.config.get('FRONTEND_URL', 'http://localhost:5173')
        # Define the target path on the frontend for handling this callback
        frontend_callback_path = '/auth/google/callback'
        # Prepare query parameters
        params = {
            'access_token': access_token,
            'refresh_token': refresh_token
            # Optionally add user info if needed directly, but fetching is better
            # 'user_id': user.user_id,
            # 'username': user.username
        }
        # Construct the full redirect URL
        redirect_url = f"{frontend_base_url}{frontend_callback_path}?{urlencode(params)}"

        print(f"Redirecting to frontend: {redirect_url}") # Debugging
        return redirect(redirect_url, code=302)

# --- Example Protected Resource ---
class UserProfile(Resource):
    @jwt_required() # This decorator requires a valid access token
    def get(self):
        # get_jwt_identity() retrieves the identity stored in the token (user_id as string)
        current_user_id_str = get_jwt_identity()
        user = User.query.get(int(current_user_id_str)) # Convert back to int for lookup

        if not user:
            return {"message": "User not found"}, 404

        # In a real app, return more profile details
        return {
            "user_id": user.user_id,
            "username": user.username,
            "email": user.email,
            "bankroll": float(user.bankroll), # Convert Decimal for JSON
            "message": "Protected data access successful"
         }, 200

# --- Parser for Password Reset Request ---
_pw_reset_request_parser = reqparse.RequestParser()
_pw_reset_request_parser.add_argument('email', type=str, required=True, help='Email cannot be blank')

# --- Parser for Setting New Password ---
_pw_reset_confirm_parser = reqparse.RequestParser()
_pw_reset_confirm_parser.add_argument('token', type=str, required=True, help='Token cannot be blank')
_pw_reset_confirm_parser.add_argument('new_password', type=str, required=True, help='New password cannot be blank')

# --- Password Reset Resources ---
class RequestPasswordReset(Resource):
    def post(self):
        data = _pw_reset_request_parser.parse_args()
        user = User.find_by_email(data['email'])

        if not user:
            # Don't reveal if email exists, standard security practice
            return {'message': 'If an account with that email exists, a reset token has been generated.'}, 200

        # Generate Token using itsdangerous
        serializer = URLSafeTimedSerializer(current_app.config['SECRET_KEY'])
        # Use email or user_id + security stamp. Email is simpler for lookup.
        token = serializer.dumps(user.email, salt='password-reset-salt')

        # TODO: Send email with token here in a real app
        # reset_url = url_for('resetpassword', token=token, _external=True) # 'resetpassword' is the endpoint name
        # send_email(user.email, "Password Reset Request", f"Click here: {reset_url}")

        print(f"--- Password Reset Token for {user.email}: {token} ---") # For testing

        # Return token FOR TESTING ONLY. Do not do this in production.
        return {
            'message': 'If an account with that email exists, a reset token has been generated.',
            'reset_token_for_testing': token # REMOVE THIS LINE IN PRODUCTION
            }, 200

class ResetPassword(Resource):
    def post(self):
        data = _pw_reset_confirm_parser.parse_args()
        serializer = URLSafeTimedSerializer(current_app.config['SECRET_KEY'])

        try:
            # Verify the token and get the email back. Set max age (e.g., 1 hour)
            email = serializer.loads(
                data['token'],
                salt='password-reset-salt',
                max_age=3600 # Token valid for 1 hour (in seconds)
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
            # Should technically not happen if token was valid, but check anyway
            return {'message': 'User not found.'}, 404

        # Set the new password
        user.set_password(data['new_password'])
        try:
            user.save_to_db() # Commits the change
        except Exception as e:
            print(f"Error saving new password: {e}")
            return {'message': 'An error occurred setting the new password.'}, 500

        return {'message': 'Password has been reset successfully.'}, 200
    

# --- Add Email Verification Resource ---
class VerifyEmail(Resource):
    def get(self, token): # Token comes from the URL
        serializer = URLSafeTimedSerializer(current_app.config['SECRET_KEY'])
        try:
             # Verify token, set reasonable max age (e.g., 1 day)
             email = serializer.loads(
                token,
                salt='email-verification-salt',
                max_age=86400 # 24 hours in seconds
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
             return {'message': 'User not found.'}, 404 # Should not happen

        if user.is_email_verified:
             return {'message': 'Email is already verified.'}, 200 # Or redirect to login

        user.is_email_verified = True
        try:
            user.save_to_db()
        except Exception as e:
            print(f"Error marking email as verified: {e}")
            return {'message': 'An error occurred during email verification.'}, 500

        # TODO: Redirect to a confirmation page or login page on the frontend
        return {'message': 'Email verified successfully!'}, 200
    
# --- Betting Resources ---

class PlaceBet(Resource):
    @jwt_required()
    def post(self):
        data = _bet_parser.parse_args()
        user = User.query.get(int(get_jwt_identity()))
        match = Match.query.get(data['match_id'])
        bet_amount = Decimal(data['amount']) # Convert from string here

        success, result = place_bet_for_user(
            user=user,
            match=match,
            team_selected=data['team_selected'],
            bet_amount=bet_amount
        )

        if success:
            return {'message': 'Bet placed successfully!', 'bet_details': result.to_dict()}, 201
        else:
            # 'result' contains the error message from the service
            return {'message': result}, 400


class UserBetList(Resource):
    @jwt_required()
    def get(self):
        current_user_id = int(get_jwt_identity())
        user = User.query.get(current_user_id)
        if not user:
            return {'message': 'User not found'}, 404

        # --- Filtering by Status (Optional) ---
        status_filter = request.args.get('status') # e.g., ?status=Pending or ?status=Settled
        query = user.bets # Access the user's bets via the relationship

        if status_filter:
            # Validate status filter if desired
            allowed_statuses = ['Pending', 'Active', 'Won', 'Lost', 'Void', 'Settled'] # Define allowed filters
            if status_filter == 'Settled': # Allow filtering for all completed bets
                 query = query.filter(Bet.status.in_(['Won', 'Lost', 'Void']))
            elif status_filter in allowed_statuses:
                 query = query.filter(Bet.status == status_filter)
            # else: ignore invalid filter? Or return error?

        # Order bets, e.g., by placement time descending
        bets = query.order_by(Bet.placement_time.desc()).all()

        return {'bets': [bet.to_dict() for bet in bets]}, 200


class UserBankrollHistoryList(Resource):
     @jwt_required()
     def get(self):
        current_user_id = int(get_jwt_identity())
        user = User.query.get(current_user_id)
        if not user:
            return {'message': 'User not found'}, 404

        # Consider adding pagination later for performance
        # page = request.args.get('page', 1, type=int)
        # per_page = request.args.get('per_page', 20, type=int)
        # history = user.bankroll_history.order_by(BankrollHistory.timestamp.desc()).paginate(page=page, per_page=per_page, error_out=False)
        # items = history.items

        history_items = user.bankroll_history.order_by(BankrollHistory.timestamp.desc()).all()

        return {
             'bankroll_history': [item.to_dict() for item in history_items]
             # Include pagination info if using paginate:
             # 'total_pages': history.pages,
             # 'current_page': history.page,
             # 'total_items': history.total
        }, 200

class AIBotBetList(Resource):
    def get(self):
        """Get all bets placed by the AI bot (LogisticsRegressionBot)"""
        # Find the AI bot user
        ai_bot = User.query.filter_by(username=AI_BOT_USERNAME).first()
        if not ai_bot:
            return {'message': f'AI Bot user "{AI_BOT_USERNAME}" not found.'}, 404

        # Get optional status filter
        status_filter = request.args.get('status')
        query = ai_bot.bets

        if status_filter:
            # Validate status filter
            allowed_statuses = ['Pending', 'Active', 'Won', 'Lost', 'Void', 'Settled']
            if status_filter == 'Settled':
                query = query.filter(Bet.status.in_(['Won', 'Lost', 'Void']))
            elif status_filter in allowed_statuses:
                query = query.filter(Bet.status == status_filter)

        # Order bets by placement time descending
        bets = query.order_by(Bet.placement_time.desc()).all()

        return {
            'ai_bot_username': AI_BOT_USERNAME,
            'ai_bot_user_id': ai_bot.user_id,
            'total_bets': len(bets),
            'bets': [bet.to_dict() for bet in bets]
        }, 200

class AIBotBankrollHistory(Resource):
    def get(self):
        """Get bankroll history for the AI bot (LogisticsRegressionBot)"""
        # Find the AI bot user
        ai_bot = User.query.filter_by(username=AI_BOT_USERNAME).first()
        if not ai_bot:
            return {'message': f'AI Bot user "{AI_BOT_USERNAME}" not found.'}, 404

        # Get the bankroll history ordered by timestamp descending (most recent first)
        history_items = ai_bot.bankroll_history.order_by(BankrollHistory.timestamp.desc()).all()

        return {
            'ai_bot_username': AI_BOT_USERNAME,
            'ai_bot_user_id': ai_bot.user_id,
            'current_bankroll': float(ai_bot.bankroll),
            'total_history_entries': len(history_items),
            'bankroll_history': [item.to_dict() for item in history_items]
        }, 200

# --- Admin/Simulation Resource ---
class SimulateResult(Resource):
    @jwt_required() # Protect this endpoint - TODO: Add admin check later if needed
    def post(self, match_id):
        data = _result_parser.parse_args()
        home_score = data['home_score']
        away_score = data['away_score']

        # Basic score validation
        if home_score < 0 or away_score < 0:
            return {'message': 'Scores cannot be negative.'}, 400

        match = Match.query.get(match_id)
        if not match:
            return {'message': 'Match not found.'}, 404

        if match.status == 'Completed':
            return {'message': 'Match has already been settled.'}, 400

        if match.status != 'Scheduled' and match.status != 'Live': # Allow settling Live matches too potentially
             # Or maybe only allow settling 'Scheduled'/'Live'? Adjust as needed.
             print(f"Attempt to settle match {match_id} with status {match.status}")
             # return {'message': f'Cannot settle match with status "{match.status}".'}, 400

        try:
            # Call the settlement logic function
            success, message = settle_bets_for_match(match_id, home_score, away_score)
            if success:
                return {'message': message or 'Match settled successfully.'}, 200
            else:
                # If settlement function returns specific error message
                return {'message': message or 'Failed to settle match.'}, 400 # Or 500 depending on error type
        except Exception as e:
            # Catch unexpected errors during settlement call
            print(f"Unexpected error calling settlement for match {match_id}: {e}")
            import traceback
            traceback.print_exc()
            return {'message': 'An internal error occurred during settlement.'}, 500
        
# --- Leaderboard Resource ---
class GlobalLeaderboard(Resource):
    # No JWT required for a public leaderboard, adjust if needed
    def get(self):
        # Get query parameters for pagination/limit (optional but good practice)
        page = request.args.get('page', 1, type=int)
        limit = request.args.get('limit', 50, type=int) # Default limit 50
        # Ensure limit isn't excessively large
        limit = min(limit, 200)

        try:
            # Query users, order by bankroll descending, paginate results
            leaderboard_page = User.query.filter(User.active == True) \
                                         .order_by(User.bankroll.desc(), User.username.asc()) \
                                         .paginate(page=page, per_page=limit, error_out=False)

            users_data = [{
                'rank': (leaderboard_page.page - 1) * leaderboard_page.per_page + i + 1, # Calculate rank
                'user_id': user.user_id,
                'username': user.username,
                'bankroll': float(user.bankroll) # Convert Decimal for JSON
             } for i, user in enumerate(leaderboard_page.items)] # Use .items with paginate

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
        
class AIPredictionsByRound(Resource):
    def get(self, year, round_number):
        logger = logging.getLogger(__name__)
        logger.info(f"Fetching AI predictions for Year {year}, Round {round_number}")
        
        # Find the round_id for the given year and round_number
        round_obj = Round.query.filter_by(year=year, round_number=round_number).first()
        if not round_obj:
            logger.warning(f"Round not found for Year {year}, Round {round_number}")
            return {'message': 'Round not found.'}, 404

        # Find all matches for this round
        matches_in_round = round_obj.matches.all()
        match_ids_in_round = [m.match_id for m in matches_in_round]
        logger.info(f"Found {len(match_ids_in_round)} matches in round: {match_ids_in_round}")

        if not match_ids_in_round:
            logger.info("No matches found in round")
            return {'predictions': {}}, 200 # No matches, so no predictions
        
        # Find AI bot user properly by username instead of hardcoding ID
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

        # Format the predictions into a dictionary keyed by match_id for easy lookup on frontend
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

# --- Function to add all routes ---

def initialize_routes(app, api):
    # Existing Match/Round routes
    api.add_resource(RoundListResource, '/api/rounds')
    api.add_resource(MatchListResource, '/api/matches', '/api/matches/upcoming')
    api.add_resource(MatchResource, '/api/matches/<int:match_id>')
    api.add_resource(UserProfile, '/api/user/profile')

    # Add Authentication routes
    api.add_resource(UserRegister, '/api/auth/register')
    api.add_resource(UserLogin, '/api/auth/login')
    api.add_resource(TokenRefresh, '/api/auth/refresh')
    api.add_resource(GoogleLogin, '/api/auth/google/login')
    api.add_resource(RequestPasswordReset, '/api/auth/request-password-reset')
    api.add_resource(ResetPassword, '/api/auth/reset-password')
    api.add_resource(VerifyEmail, '/api/auth/verify-email/<string:token>', endpoint='verifyemail')

    # IMPORTANT: Endpoint name 'googleauthcallback' must match url_for in GoogleLogin
    api.add_resource(GoogleAuthCallback, '/api/auth/google/callback', endpoint='googleauthcallback')

    # Add Betting routes
    api.add_resource(PlaceBet, '/api/bets/place')
    api.add_resource(UserBetList, '/api/bets') # Endpoint to view user's bets
    api.add_resource(UserBankrollHistoryList, '/api/user/bankroll-history') # Endpoint for history
    api.add_resource(AIBotBetList, '/api/ai-bot/bets') # Endpoint for AI bot's bets
    api.add_resource(AIBotBankrollHistory, '/api/ai-bot/bankroll-history') # Endpoint for AI bot's bankroll history

    #simulate reults routes
    api.add_resource(SimulateResult, '/api/admin/matches/<int:match_id>/simulate-result')

    # Add Leaderboard route
    api.add_resource(GlobalLeaderboard, '/api/leaderboard/global')

    # Add AI Predictions route
    api.add_resource(AIPredictionsByRound, '/api/ai-predictions/year/<int:year>/round/<int:round_number>')

    # --- Add SSE Route directly to app ---
    @app.route('/api/stream/updates')
    def sse_stream():
        # stream_with_context is important for generators with app context needs
        # though our current generator is simple and might not need it explicitly
        # if it doesn't access current_app or g.
        return Response(stream_with_context(sse_event_stream_generator()), mimetype='text/event-stream')

    print("---API AND SSE ROUTES INITIALISED---")

     # Keep for debugging startup

    