# app/api/routes.py
from flask import request, redirect, url_for, session, current_app
from flask_restful import Resource, reqparse
from flask_jwt_extended import create_access_token, create_refresh_token, jwt_required, get_jwt_identity
from app.models import Round, Match, User
from app import db, oauth
from datetime import datetime, timezone
from itsdangerous import URLSafeTimedSerializer, SignatureExpired, BadTimeSignature
import secrets

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

# --- Authentication Resources ---

class UserRegister(Resource):
    def post(self):
        data = _user_parser.parse_args()

        # Check if username or email already exists
        if User.find_by_username(data['username']):
            return {'message': 'A user with that username already exists'}, 400
        if User.find_by_email(data['email']):
            return {'message': 'A user with that email already exists'}, 400

        user = User(
            username=data['username'],
            email=data['email'].lower(), # Store email in lowercase
            is_email_verified = False
        )
        user.set_password(data['password'])
        # user.is_email_verified = False # Add email verification later

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
             # Redirect to frontend login page with error message
             # For now, return a simple error
             return {'message': 'Error authorizing with Google.'}, 401


        # Get user info from Google using the obtained token
        # The userinfo endpoint is standard for OpenID Connect
        user_info = oauth.google.get('https://openidconnect.googleapis.com/v1/userinfo').json()
        # Or use: user_info = token.get('userinfo') if using OpenID Connect features of Authlib fully

        google_id = user_info.get('sub') # 'sub' is the standard OpenID subject identifier (Google ID)
        email = user_info.get('email')
        # picture = user_info.get('picture')
        # first_name = user_info.get('given_name')
        username = user_info.get('email').split('@')[0] # Use email prefix as default username

        if not email or not google_id:
             return {'message': 'Could not retrieve required info from Google.'}, 400

        # Check if user exists by Google ID
        user = User.find_by_google_id(google_id)

        if not user:
            # Check if user exists by email (maybe registered locally before)
            user = User.find_by_email(email)
            if user:
                # User exists via email, link Google ID
                if user.google_id is None:
                    user.google_id = google_id
                # Optionally update other fields if needed
                if not user.is_email_verified: # Google verifies email
                     user.is_email_verified = True
            else:
                # User doesn't exist, create a new one
                # Ensure generated username is unique
                temp_username = username
                counter = 1
                while User.find_by_username(temp_username):
                    temp_username = f"{username}{counter}"
                    counter += 1
                username = temp_username

                user = User(
                    username=username,
                    email=email.lower(),
                    google_id=google_id,
                    is_email_verified=True # Email verified by Google
                    # Set default bankroll, etc.
                )
            # Save new user or updated user link
            try:
                 user.save_to_db()
            except Exception as e:
                 print(f"Error saving Google user: {e}")
                 return {'message': 'An error occurred processing Google login.'}, 500

        # User now exists (either found, linked, or created)
        # Create JWT tokens for the user session
        access_token = create_access_token(identity=str(user.user_id), fresh=True)
        refresh_token = create_refresh_token(identity=str(user.user_id))

        # Update last_login time
        user.last_login = datetime.now(timezone.utc)
        db.session.commit()

        # TODO: Redirect to Frontend with tokens
        # For now, just return the tokens (less secure for redirect flow)
        # In a real app, you'd redirect to a frontend URL like:
        # frontend_url = f"http://localhost:3000/auth/callback?access_token={access_token}&refresh_token={refresh_token}"
        # return redirect(frontend_url)

        return {
            'message': 'Successfully logged in with Google.',
            'access_token': access_token,
            'refresh_token': refresh_token,
            'user_id': user.user_id,
            'username': user.username
         }, 200

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

    print("--- API Routes Initialized ---") # Keep for debugging startup

    