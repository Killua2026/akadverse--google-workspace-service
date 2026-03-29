from google import genai 
from pydantic import BaseModel # For data validation and structured event handling

import os # For file handling
import logging
import time
import uuid
# IMPORTANT: Allow HTTP traffic for local testing ONLY. 
# Remove or set to '0' before deploying to production!
os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'

import json # For token storage
from typing import Any
from fastapi import FastAPI, Request, HTTPException # For handling web requests and exceptions
from fastapi.responses import RedirectResponse # For redirecting users to Google's OAuth2 consent screen
from google_auth_oauthlib.flow import Flow # For managing the OAuth2 flow
from google.oauth2.credentials import Credentials # For handling OAuth2 credentials
from google.auth.transport.requests import Request as GoogleRequest # For refreshing tokens
from sheets_handler import AkadVerseSheetManager # Custom module to manage Google Sheets interactions
from drive_handler import AkadVerseDriveManager # Custom module to manage Google Drive interactions
import uvicorn # For running the FastAPI app

app = FastAPI(title="AkadVerse Workspace Integration Service")

logging.basicConfig(
    level=getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO),
    format="%(message)s",
)
logger = logging.getLogger("akadverse.chat")

CLIENT_SECRETS_FILE = "client_secret.json"
SCOPES = [
    'https://www.googleapis.com/auth/drive.file',
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/documents'
]
TOKEN_STORE = "token.json"

# Preferred order when multiple Gemini models are available.
# Discovery tries to find the first valid model from this list.
PREFERRED_GEMINI_MODELS = [
    "gemini-2.5-pro",
    "gemini-2.5-flash",
    "gemini-2.0-flash",
    "gemini-1.5-pro",
    "gemini-1.5-flash",
]

# If discovery fails due to API/version/network issues, we still attempt these models.
DISCOVERY_FALLBACK_MODELS = [
    "gemini-2.5-flash",
    "gemini-2.0-flash",
    "gemini-1.5-pro",
]

# NEW: A temporary dictionary to store our OAuth flow in memory!
# This solves the "Missing code verifier" PKCE error.
oauth_session_store = {}


def log_structured_event(event: str, **fields: Any) -> None:
    """Emit one-line JSON logs for easier filtering in production aggregators."""
    payload = {
        "event": event,
        "service": "akadverse-workspace-service",
        "timestamp": int(time.time()),
    }
    payload.update(fields)
    logger.info(json.dumps(payload, default=str))


def _get_attr(obj: Any, key: str, default: Any = None) -> Any:
    """Safely get a field from both dict-like and object-like model metadata."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _normalize_model_name(name: str) -> str:
    """Convert API names like 'models/gemini-2.5-flash' to 'gemini-2.5-flash'."""
    if not name:
        return ""
    return name[7:] if name.startswith("models/") else name


def _supports_generate_content(model_info: Any) -> bool:
    """Detect whether a model supports text generation across SDK schema variants."""
    methods = _get_attr(model_info, "supported_generation_methods", [])
    actions = _get_attr(model_info, "supported_actions", [])

    method_tokens = {str(item).replace("_", "").lower() for item in (methods or [])}
    action_tokens = {str(item).replace("_", "").lower() for item in (actions or [])}

    return (
        "generatecontent" in method_tokens
        or "generatecontent" in action_tokens
        or not method_tokens and not action_tokens
    )


def discover_best_gemini_model(client: genai.Client) -> str:
    """Discover and select the most suitable Gemini model available to this API key."""
    discovered = list(client.models.list())
    if not discovered:
        raise RuntimeError("Model discovery returned an empty list.")

    viable_models: list[str] = []
    for model_info in discovered:
        raw_name = _get_attr(model_info, "name", "")
        normalized_name = _normalize_model_name(str(raw_name))
        if not normalized_name:
            continue

        if "gemini" not in normalized_name.lower():
            continue

        if not _supports_generate_content(model_info):
            continue

        viable_models.append(normalized_name)

    if not viable_models:
        raise RuntimeError("No generation-capable Gemini models were discovered for this API key.")

    # Honor explicit preference order first, then use first discovered model.
    for preferred_model in PREFERRED_GEMINI_MODELS:
        if preferred_model in viable_models:
            return preferred_model

    return viable_models[0]


def _classify_genai_error(exc: Exception) -> tuple[int, str]:
    """Map provider errors to safer HTTP responses without leaking sensitive details."""
    message = str(exc).lower()
    if "api key" in message or "permission" in message or "unauthorized" in message or "authentication" in message:
        return 401, "Gemini authentication failed. Verify the Google API key and permissions."
    if "quota" in message or "rate" in message or "429" in message:
        return 429, "Gemini quota/rate limit reached. Please retry later."
    if "timeout" in message or "temporar" in message or "unavailable" in message or "503" in message:
        return 503, "Gemini service is temporarily unavailable. Please retry later."
    if "model" in message and "not found" in message:
        return 502, "Gemini model selection failed. No compatible model could be used."
    return 500, "Gemini request failed due to an unexpected provider error."

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
        # prompt='consent' forces Google to re-issue a refresh_token on every login.
        # Without this, repeat logins skip the refresh_token, producing an incomplete
        # token.json that causes "missing fields refresh_token" errors in other services.
        authorization_url, state = flow.authorization_url(
            access_type='offline',
            include_granted_scopes='true',
            prompt='consent'
        )
        
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
    

@app.post("/webhook/setup-drive")
async def setup_drive_folders():
    """
    Tests the Google Drive integration by creating the /AkadVerse/2026/Notes/ structure.
    """
    try:
        creds = get_credentials()
        if not creds:
            raise HTTPException(status_code=401, detail="User not authenticated with Google")
        
        # Initialize our new manager
        drive_manager = AkadVerseDriveManager(creds)
        
        # Trigger the folder creation logic
        notes_folder_id = drive_manager.setup_akadverse_structure(year="2026")
        
        if notes_folder_id:
            return {
                "status": "success", 
                "message": "AkadVerse folder structure verified/created successfully!",
                "notes_folder_id": notes_folder_id
            }
        else:
            raise HTTPException(status_code=500, detail="Failed to create folder structure. Check terminal logs.")
            
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Drive setup error: {str(e)}")

@app.post("/webhook/save-generated-note")
async def handle_save_note(event: dict):
    """
    Simulates a Kafka Consumer receiving a 'note.generated' event from the Notes Creator.
    Expected Payload: {"title": "CSC332 Module 1 Summary", "content": "Here are your AI generated notes..."}
    """
    try:
        creds = get_credentials()
        if not creds:
            raise HTTPException(status_code=401, detail="User not authenticated with Google")
        
        drive_manager = AkadVerseDriveManager(creds)
        
        # 1. Ensure the folder structure exists and get the target ID
        notes_folder_id = drive_manager.setup_akadverse_structure(year="2026")
        
        if not notes_folder_id:
            raise HTTPException(status_code=500, detail="Could not locate or create the target folder.")
            
        # 2. Extract data from the event and create the document
        doc_title = event.get("title", "Untitled AkadVerse Note")
        doc_content = event.get("content", "Empty content.")
        
        doc_link = drive_manager.create_note_doc(
            title=doc_title, 
            content=doc_content, 
            folder_id=notes_folder_id
        )
        
        if doc_link:
            return {
                "status": "success", 
                "message": "Note successfully saved to Google Drive",
                "link": doc_link
            }
        else:
            raise HTTPException(status_code=500, detail="Failed to upload document to Drive.")
            
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Webhook processing error: {str(e)}")
    
# ==========================================
# TIER 4: CONTEXTUAL CHAT WITH GOOGLE-GENAI SDK
# This endpoint demonstrates how to use the new google-genai SDK for contextual chat.
# The frontend can send the user's query along with injected platform context (like course info, grades, etc.)
# The AI will use this context to provide more personalized and relevant responses.
#==========================================

# 1. Data Model for Contextual Chat
class ChatRequest(BaseModel):
    user_query: str
    context_data: dict # Injected platform metadata (courses, levels, etc.)
    google_api_key: str # Pass this from the frontend/Swagger for testing
    format: str = "plain" # Response format: "plain" or "markdown"

@app.post("/chat/contextual")
async def contextual_chat(request: ChatRequest):
    """
    Tier 4: Gemini Chat with Platform Context Injection.
    Uses the new google-genai SDK for optimized performance in Python 3.12.
    """
    request_id = str(uuid.uuid4())

    if not request.google_api_key or not request.google_api_key.strip():
        log_structured_event(
            "chat.validation_failed",
            request_id=request_id,
            reason="missing_google_api_key",
        )
        raise HTTPException(status_code=400, detail="google_api_key is required.")

    log_structured_event(
        "chat.request_received",
        request_id=request_id,
        query_length=len(request.user_query or ""),
        context_keys=sorted((request.context_data or {}).keys()),
    )

    try:
        client = genai.Client(api_key=request.google_api_key.strip())
    except Exception as e:
        log_structured_event(
            "chat.client_init_failed",
            request_id=request_id,
            error_type=type(e).__name__,
            error_message=str(e),
        )
        status_code, message = _classify_genai_error(e)
        raise HTTPException(status_code=status_code, detail=message)

    # Build prompt with injected platform context.
    context_str = json.dumps(request.context_data or {})
    format_instruction = ""
    if request.format.lower() == "markdown":
        format_instruction = "Format your response using Markdown syntax with appropriate headers, lists, bold/italic text, and code blocks where relevant. "
    
    system_prompt = (
        "You are the AkadVerse AI Assistant. "
        f"Student Context: {context_str}. "
        "Provide academic guidance based on this data. "
        f"{format_instruction}"
        "Constraints: Never use em-dashes. Use a professional tone."
    )
    full_prompt = f"{system_prompt}\n\nUser Question: {request.user_query}"

    selected_model = None
    discovery_warning = None
    model_attempts: list[str] = []

    try:
        selected_model = discover_best_gemini_model(client)
        model_attempts.append(selected_model)
        log_structured_event(
            "chat.model_discovery_succeeded",
            request_id=request_id,
            selected_model=selected_model,
        )
    except Exception as e:
        discovery_warning = f"Model discovery failed; using fallback list. Reason: {str(e)}"
        log_structured_event(
            "chat.model_discovery_failed",
            request_id=request_id,
            error_type=type(e).__name__,
            error_message=str(e),
        )

    for fallback_model in DISCOVERY_FALLBACK_MODELS:
        if fallback_model not in model_attempts:
            model_attempts.append(fallback_model)

    log_structured_event(
        "chat.model_attempt_plan",
        request_id=request_id,
        attempted_models=model_attempts,
        discovery_mode="dynamic" if selected_model else "fallback",
    )

    generation_errors: list[str] = []
    for attempt_index, model_name in enumerate(model_attempts, start=1):
        attempt_started_at = time.time()
        log_structured_event(
            "chat.model_attempt_started",
            request_id=request_id,
            attempt_index=attempt_index,
            model=model_name,
        )

        try:
            response = client.models.generate_content(
                model=model_name,
                contents=full_prompt
            )

            reply_text = getattr(response, "text", None)
            if not reply_text:
                raise RuntimeError("Gemini response did not include text output.")

            duration_ms = int((time.time() - attempt_started_at) * 1000)
            log_structured_event(
                "chat.model_attempt_succeeded",
                request_id=request_id,
                attempt_index=attempt_index,
                model=model_name,
                duration_ms=duration_ms,
                reply_length=len(reply_text),
            )

            # Kafka Mock: Simulate event for the Insight Engine
            print("[KAFKA MOCK] Published event 'agent.message.relayed' for user")

            result = {
                "status": "success",
                "reply": reply_text,
                "model_used": model_name,
                "model_discovery": "dynamic" if selected_model else "fallback",
                "format": request.format.lower(),
            }
            if discovery_warning:
                result["warning"] = discovery_warning
            return result

        except Exception as e:
            duration_ms = int((time.time() - attempt_started_at) * 1000)
            log_structured_event(
                "chat.model_attempt_failed",
                request_id=request_id,
                attempt_index=attempt_index,
                model=model_name,
                duration_ms=duration_ms,
                error_type=type(e).__name__,
                error_message=str(e),
            )
            generation_errors.append(f"{model_name}: {str(e)}")

    status_code, message = _classify_genai_error(Exception("\n".join(generation_errors)))
    log_structured_event(
        "chat.request_failed",
        request_id=request_id,
        status_code=status_code,
        attempted_models=model_attempts,
        error_count=len(generation_errors),
    )
    raise HTTPException(
        status_code=status_code,
        detail={
            "message": message,
            "attempted_models": model_attempts,
            "discovery_warning": discovery_warning,
        },
    )

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8002)
    
# run with:  uvicorn main:app --host 127.0.0.1 --port 8002 --reload