# app/api/routes.py
from flask import request
from flask_restful import Resource # Make sure Resource is imported
from app.models import Round, Match
from app import db
from datetime import datetime, timezone

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