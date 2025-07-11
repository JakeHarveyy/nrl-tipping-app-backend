# app/config.py
import os
from dotenv import load_dotenv

# Load environment variables from .env file
basedir = os.path.abspath(os.path.dirname(__file__))
load_dotenv(os.path.join(basedir, '..', '.env')) # Look for .env file one level up

class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY')
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL')
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    # Add other configurations as needed
    
    GOOGLE_CLIENT_ID = os.environ.get('GOOGLE_CLIENT_ID')
    GOOGLE_CLIENT_SECRET = os.environ.get('GOOGLE_CLIENT_SECRET')
    GOOGLE_REDIRECT_URI = os.environ.get('GOOGLE_REDIRECT_URI')

    FRONTEND_URL = os.environ.get('FRONTEND_URL') or 'http://localhost:5173'

class DevelopmentConfig(Config):
    DEBUG = True

class ProductionConfig(Config):
    DEBUG = False
    # Heroku provides DATABASE_URL but it might use postgres:// instead of postgresql://
    # Fix the URL format for SQLAlchemy 1.4+
    database_url = os.environ.get('DATABASE_URL')
    if database_url and database_url.startswith('postgres://'):
        database_url = database_url.replace('postgres://', 'postgresql://', 1)
    SQLALCHEMY_DATABASE_URI = database_url
    # Add production-specific settings here

# You might add TestingConfig later

# Dictionary to access configs by name
config_by_name = dict(
    development=DevelopmentConfig,
    prod=ProductionConfig,
    production=ProductionConfig  # Heroku might use "production"
)