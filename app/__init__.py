# app/__init__.py
import os
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_restful import Api  # <<< Import Api
from flask_cors import CORS
from flask_bcrypt import Bcrypt # Import Bcrypt
from flask_jwt_extended import JWTManager # Import JWTManager
from authlib.integrations.flask_client import OAuth # Import OAuth

from app.config import config_by_name

db = SQLAlchemy()
migrate = Migrate()
api_restful = Api() # <<< Instantiate Api HERE, outside the factory
bcrypt = Bcrypt() # Instantiate Bcrypt
jwt = JWTManager() # Instantiate JWTManager
oauth = OAuth() # Instantiate OAuth

def create_app(config_name=None):
    """Application Factory Pattern"""
    if config_name is None:
        config_name = os.getenv('FLASK_ENV', 'development')

    app = Flask(__name__)
    app.config.from_object(config_by_name[config_name])

    print(f"--- Using database URI: {app.config.get('SQLALCHEMY_DATABASE_URI')} ---") # Keep this for debugging

    # Initialize extensions that need the app object directly
    db.init_app(app)
    migrate.init_app(app, db)
    bcrypt.init_app(app) # Initialize Bcrypt with app
    jwt.init_app(app) # Initialize JWTManager with app
    oauth.init_app(app) # Initialize OAuth with app
    CORS(app, resources={r"/api/*": {"origins": "*"}})

    # Register Google OAuth client with Authlib
    oauth.register(
        name='google',
        client_id=app.config.get('GOOGLE_CLIENT_ID'),
        client_secret=app.config.get('GOOGLE_CLIENT_SECRET'),
        server_metadata_url='https://accounts.google.com/.well-known/openid-configuration', # Discovery URL
        client_kwargs={
            'scope': 'openid email profile' # Scopes determine what info you ask for
        }
    )


    # Import models AFTER db is initialized IF they rely on db instance directly at import time
    # (Generally safer to import them here or inside routes where needed)
    from app import models

    # --- Initialise API Routes ---
    # Import the function that initializes routes
    from app.api.routes import initialize_routes
    api_restful.init_app(app)
    initialize_routes(api_restful)
    print("--- API Routes Initialized ---")
    # ---------------------

    # Example basic route (can be removed later)
    @app.route('/health')
    def health_check():
        # Check DB connection maybe?
        try:
             db.session.execute(db.text('SELECT 1'))
             return "OK", 200
        except Exception as e:
             print(f"Health check DB connection failed: {e}")
             return "DB Error", 500
    
        from app.api.routes import initialize_routes
    api_restful.init_app(app)
    initialize_routes(api_restful)

    # --- Add Temporary Test Route ---
    @app.route('/api/test')
    def api_test_route():
        return "Flask test route OK", 200
    
    print("--- Registered Routes ---")
    for rule in app.url_map.iter_rules():
        print(f"Endpoint: {rule.endpoint}, Methods: {list(rule.methods)}, Path: {rule.rule}")
    print("-----------------------")

    return app