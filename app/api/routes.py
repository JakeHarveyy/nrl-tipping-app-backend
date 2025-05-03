# app/api/routes.py
from flask import request, redirect, url_for, session, current_app
from flask_restful import Resource, reqparse
from flask_jwt_extended import create_access_token, create_refresh_token, jwt_required, get_jwt_identity
from app.models import Round, Match, User, Bet, BankrollHistory
from app import db, oauth
from datetime import datetime, timezone
from itsdangerous import URLSafeTimedSerializer, SignatureExpired, BadTimeSignature
import secrets
from urllib.parse import urlencode
from decimal import Decimal, InvalidOperation
from .settlement import settle_bets_for_match


class RoundListResource(Resource): # Ensure it inherits from Resource
    def get(self):
        """Get list of all rounds"""
        # ... (implementation as before) ...
        rounds = Round.query.order_by(Round.year, Round.round_number).all()
        return {'rounds': [r.to_dict() for r in rounds]}, 200


class MatchListResource(Resource): # Ensure it inherits from Resource
    def get(self):
        """Get list of matches, optionally filtered"""
        # ... (implementation as before) ...
        # Ensure filtering logic is correct
        status_filter = request.args.get('status', 'Scheduled')
        round_filter = request.args.get('round_number', type=int)
        year_filter = request.args.get('year', type=int, default=datetime.now(timezone.utc).year)

        query = Match.query
        if status_filter:
            query = query.filter(Match.status == status_filter)

        if round_filter is not None:
            query = query.join(Round).filter(Round.round_number == round_filter, Round.year == year_filter)
        else:
             current_time = datetime.now(timezone.utc)
             # Maybe filter by start_time > now OR status = 'Scheduled'/'Live' for upcoming?
             query = query.filter(Match.start_time >= current_time)
             # Or query = query.filter(Match.status.in_(['Scheduled', 'Live']))


        query = query.order_by(Match.start_time.asc())
        matches = query.all()
        return {'matches': [m.to_dict() for m in matches]}, 200


class MatchResource(Resource): # Ensure it inherits from Resource
     def get(self, match_id):
         """Get details for a specific match"""
         match = Match.query.get_or_404(match_id)
         return {'match': match.to_dict()}, 200

# This function ADDS routes to the api object passed in from create_app
def initialize_routes(api): # Ensure 'api' parameter is used
    # Check paths carefully for typos!
    api.add_resource(RoundListResource, '/api/rounds')
    api.add_resource(MatchListResource, '/api/matches', '/api/matches/upcoming') # Check these paths
    api.add_resource(MatchResource, '/api/matches/<int:match_id>') # Check this path
    print("--- API Routes Initialized ---") # Add a print statement here

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
        # Construct the redirect URI dynamically
        # Use url_for with _external=True to get the full URL
        redirect_uri = url_for('googleauthcallback', _external=True) # Match endpoint name below
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
    @jwt_required() # Protect this endpoint
    def post(self):
        data = _bet_parser.parse_args()
        current_user_id = int(get_jwt_identity()) # Identity is string, convert to int
        user = User.query.get(current_user_id)

        if not user:
            return {'message': 'User not found'}, 404

        match = Match.query.get(data['match_id'])
        if not match:
            return {'message': 'Match not found'}, 404

        # --- Validations ---
        # 1. Is match still open for betting?
        if match.start_time <= datetime.now(timezone.utc):
            return {'message': 'Betting is closed for this match (match has started or finished).'}, 400
        if match.status != 'Scheduled':
             return {'message': f'Match status is "{match.status}", betting only allowed on "Scheduled" matches.'}, 400

        # 2. Is team selection valid?
        selected_odds = None
        if data['team_selected'] == match.home_team:
            selected_odds = match.home_odds
        elif data['team_selected'] == match.away_team:
            selected_odds = match.away_odds
        else:
            return {'message': f"Invalid team selected. Choose '{match.home_team}' or '{match.away_team}'."}, 400

        if selected_odds is None:
             return {'message': 'Odds not available for the selected team at this time.'}, 400

        # 3. Is amount valid?
        try:
            bet_amount = Decimal(data['amount'])
            if bet_amount <= 0:
                raise ValueError("Amount must be positive.")
            # Ensure precision (e.g., allow only 2 decimal places)
            if bet_amount.as_tuple().exponent < -2:
                 raise ValueError("Amount cannot have more than two decimal places.")
        except (ValueError, TypeError) as e:
             # Catches Decimal conversion errors and explicit ValueErrors
             print(f"Invalid bet amount format: {data['amount']}. Error: {e}")
             return {'message': 'Invalid bet amount format. Must be a positive number with up to two decimal places.'}, 400


        # 4. Does user have sufficient funds?
        if user.bankroll < bet_amount:
            return {'message': f'Insufficient funds. Your balance is ${user.bankroll:.2f}'}, 400
        # --- End Validations ---

        # --- Calculate Payout ---
        potential_payout = bet_amount * selected_odds # Decimal multiplication

        # --- Database Transaction ---
        try:
            # Start transaction explicitly if needed, or rely on commit/rollback
            previous_balance = user.bankroll
            new_balance = previous_balance - bet_amount

            # 1. Update user bankroll
            user.bankroll = new_balance

            # 2. Create Bet record
            new_bet = Bet(
                user_id=user.user_id,
                match_id=match.match_id,
                round_id=match.round_id, # Get round_id from the match
                team_selected=data['team_selected'],
                amount=bet_amount,
                odds_at_placement=selected_odds,
                potential_payout=potential_payout,
                status='Pending',
                placement_time=datetime.now(timezone.utc)
            )
            db.session.add(new_bet)
            db.session.flush() # Flush to get new_bet.bet_id if needed now

            # 3. Create BankrollHistory record
            history_entry = BankrollHistory(
                user_id=user.user_id,
                round_number=match.round.round_number, # Get round number from match->round relationship
                change_type='Bet Placement',
                related_bet_id=new_bet.bet_id, # Link history to the bet
                amount_change=-bet_amount, # Negative for placement
                previous_balance=previous_balance,
                new_balance=new_balance,
                timestamp=new_bet.placement_time # Use bet placement time for consistency
            )
            db.session.add(history_entry)

            # 4. Commit all changes
            db.session.commit()

            return {'message': 'Bet placed successfully!', 'bet_details': new_bet.to_dict()}, 201

        except Exception as e:
            db.session.rollback() # Rollback on any error
            print(f"ERROR placing bet for user {user.username}: {e}")
            import traceback
            traceback.print_exc()
            return {'message': 'An error occurred placing the bet.'}, 500
        # --- End Transaction ---


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

# --- Function to add all routes ---

def initialize_routes(api):
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

    #simulate reults routes
    api.add_resource(SimulateResult, '/api/admin/matches/<int:match_id>/simulate-result')

    # Add Leaderboard route
    api.add_resource(GlobalLeaderboard, '/api/leaderboard/global')

    print("--- API Routes Initialized ---") # Keep for debugging startup

    