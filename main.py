import os # For file handling
# IMPORTANT: Allow HTTP traffic for local testing ONLY. 
# Remove or set to '0' before deploying to production!
os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'

import json # For token storage
from fastapi import FastAPI, Request, HTTPException # For handling web requests and exceptions
from fastapi.responses import RedirectResponse # For redirecting users to Google's OAuth2 consent screen
from google_auth_oauthlib.flow import Flow # For managing the OAuth2 flow
from google.oauth2.credentials import Credentials # For handling OAuth2 credentials
from google.auth.transport.requests import Request as GoogleRequest # For refreshing tokens
from sheets_handler import AkadVerseSheetManager # Custom module to manage Google Sheets interactions
import uvicorn # For running the FastAPI app

app = FastAPI(title="AkadVerse Workspace Integration Service")

CLIENT_SECRETS_FILE = "client_secret.json"
SCOPES = [
    'https://www.googleapis.com/auth/drive.file',
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/documents'
]
TOKEN_STORE = "token.json"

# NEW: A temporary dictionary to store our OAuth flow in memory!
# This solves the "Missing code verifier" PKCE error.
oauth_session_store = {}

def get_credentials():
    """Helper to load or refresh stored user tokens."""
    creds = None
    if os.path.exists(TOKEN_STORE):
        creds = Credentials.from_authorized_user_file(TOKEN_STORE, SCOPES)
    
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(GoogleRequest())
        else:
            return None
    return creds

@app.get("/login")
async def login():
    """Starts the OAuth2 flow and saves the stateful Flow object."""
    try:
        flow = Flow.from_client_secrets_file(
            CLIENT_SECRETS_FILE, scopes=SCOPES,
            redirect_uri='http://localhost:8002/callback'
        )
        # Generate the auth URL and the unique state string
        authorization_url, state = flow.authorization_url(access_type='offline', include_granted_scopes='true')
        
        # Store the exact flow object in our memory dictionary using the state as the key
        oauth_session_store[state] = flow
        
        return RedirectResponse(authorization_url)
    
    except Exception as e:
        # Robust error handling for unexpected initialization failures
        raise HTTPException(status_code=500, detail=f"Login initiation failed: {str(e)}")

@app.get("/callback")
async def callback(request: Request):
    """Handles the redirect, retrieves the saved Flow, and fetches the token."""
    try:
        # Extract the state from Google's redirect URL
        state = request.query_params.get("state")
        
        # Retrieve the EXACT same flow object we created in /login
        flow = oauth_session_store.get(state)
        
        if not flow:
            raise HTTPException(status_code=400, detail="Session expired or invalid state. Please try logging in again.")
        
        # Exchange the authorization code for an access token (now has the code verifier!)
        flow.fetch_token(authorization_response=str(request.url))
        creds = flow.credentials
        
        # Save the credentials to token.json
        with open(TOKEN_STORE, 'w') as token:
            token.write(creds.to_json())
            
        # Clean up the memory store to prevent memory leaks
        del oauth_session_store[state]
            
        return {
            "status": "success", 
            "message": "AkadVerse is connected! Check your folder for token.json."
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"OAuth callback failed: {str(e)}")
    
@app.post("/webhook/assessment-completed")
async def handle_assessment_event(event: dict):
    """
    Simulates a Kafka Consumer receiving an 'assessment.completed' event.
    Expected Payload: {"spreadsheet_id": "...", "course": "CSC332", "score": 85, "grade": "A"}
    """
    creds = get_credentials()
    if not creds:
        raise HTTPException(status_code=401, detail="User not authenticated with Google")
    
    manager = AkadVerseSheetManager(creds)
    success = manager.log_quiz_result(
        spreadsheet_id=event.get("spreadsheet_id"),
        course_name=event.get("course"),
        score=event.get("score"),
        grade=event.get("grade")
    )
    
    if success:
        return {"status": "event_processed", "message": "Quiz result synced to Google Sheets"}
    else:
        raise HTTPException(status_code=500, detail="Failed to sync to Google Sheets")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8002)
