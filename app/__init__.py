# app/__init__.py
import os
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_restful import Api  # <<< Import Api
from flask_cors import CORS

from app.config import config_by_name

db = SQLAlchemy()
migrate = Migrate()
api_restful = Api() # <<< Instantiate Api HERE, outside the factory

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
    CORS(app, resources={r"/api/*": {"origins": "*"}})

    # Import models AFTER db is initialized IF they rely on db instance directly at import time
    # (Generally safer to import them here or inside routes where needed)
    from app import models

    # --- CRITICAL PART ---
    # Import the function that initializes routes
    from app.api.routes import initialize_routes
    # Initialize Flask-RESTful with the app AFTER app creation
    api_restful.init_app(app)
    # Call the function to add resources to the api_restful object
    initialize_routes(api_restful)
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