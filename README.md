# AkadVerse: Google Workspace Integration Service

**Tier 4 Data Pipeline / Integration | Microservice Port: `8002`**

A secure, event-driven data pipeline that bridges AkadVerse platform actions with Google Workspace, featuring dynamic AI model discovery and context-aware academic assistance for Covenant University students.

## Table of Contents
- [What This Microservice Does](#what-this-microservice-does)
- [Architecture Overview](#architecture-overview)
- [Prerequisites](#prerequisites)
- [Getting Your API Credentials](#getting-your-api-credentials)
- [Installation](#installation)
- [Running the Server](#running-the-server)
- [API Endpoints](#api-endpoints)
- [Testing with Swagger UI](#testing-with-swagger-ui)
- [Example Test Inputs](#example-test-inputs)
- [Understanding the Responses](#understanding-the-responses)
- [Generated Files](#generated-files)
- [Common Errors and Fixes](#common-errors-and-fixes)
- [Project Structure](#project-structure)

## What This Microservice Does

This service is a Tier 4 component of the AkadVerse AI-first e-learning platform, living inside the Platform Core.

**Core Functions:**

*   **OAuth2 Orchestration:** Securely manages per-user Google tokens to enable AkadVerse to act as a silent assistant in a student's personal Drive.
*   **Automated Documentation:** Responds to `note.generated` and `assessment.completed` events by creating structured folders, Google Docs, and Sheets entries.
*   **Dynamic Contextual Chat:** Provides an embedded chat interface that uses Platform Context Injection to understand a student's level, courses, and performance before responding.
*   **Dynamic Model Discovery:** Automatically identifies and selects the best available Gemini model (Pro or Flash) based on the current API quota and availability.

**Key Design Decisions:**

*   **SDK Choice:** Migrated to the `google-genai` (March 2026) SDK for native Python 3.12 compatibility and streamlined multimodal support.
*   **Idempotent Drive Logic:** Folder creation logic checks for existing directories (`/AkadVerse/[Year]/Notes duplicate clutter.
*   **Markdown-Native:** Supports a format flag to return AI responses in structured Markdown for high-quality frontend rendering.

## Architecture Overview

```
Event Source (Kafka)
        |
        v
[AkadVerse Integration Service] (Port 8002)
        |
        |-- [Auth Manager] (OAuth2 Handshake + Refresh Tokens)
        |-- [Model Registry] (Dynamic Discovery & Fallback Logic)
        |-- [Drive Manager] (Folder nesting & Doc creation)
        |-- [Sheet Manager] (Asynchronous row logging)
        |
        v
Google Workspace Ecosystem (Drive, Docs, Sheets)
```

| Component      | Technology             | Purpose                      |
| :------------- | :--------------------- | :--------------------------- |
| API Framework  | FastAPI (Python 3.12)  | Async REST and Webhook endpoints |
| AI Orchestration | google-genai (v2026.03) | LLM interaction and model discovery |
| Google SDK     | google-api-python-client | Low-level Drive/Sheets manipulation |
| Security       | OAuth 2.0 + PKCE       | Secure user-permissioned access |

## Prerequisites

*   Python 3.10 or higher (3.12 recommended)
*   `pip`
*   A Google Cloud Project with Drive, Sheets, and Docs APIs enabled
*   A valid `client_secret.json` from the Google Cloud Console
*   A Google Gemini API Key from Google AI Studio

## Getting Your API Credentials

1.  Go to the [Google Cloud Console](https://console.cloud.google.com/).
2.  Create an OAuth 2.0 Client ID for a Web Application.
3.  Set the Authorized Redirect URI to `http://localhost:8002/callback`.
4.  Download the JSON, rename it to `client_secret.json`, and place it in the project root.
5.  In the OAuth Consent Screen, add your testing email to the Test Users list.

## Installation

**Step 1 -- Set up your project folder**

```bash
mkdir akadverse-workspace-service && cd akadverse-workspace-service
```

**Step 2 -- Create and activate a virtual environment**

```bash
python -m venv venv
venv\Scripts\activate # Windows
source venv/bin/activate # macOS/Linux
```

**Step 3 -- Install dependencies**

```bash
pip install -r requirements.txt
```

## Running the Server

```bash
uvicorn main:app --host 127.0.0.1 --port 8002 --reload
```

## API Endpoints

### 1. `GET /login`

**What it does:** Starts the OAuth2 flow. Users are redirected to Google's consent screen to link their account to AkadVerse.

### 2. `POST /chat/contextual`

**What it does:** Context-aware chat using Platform Context Injection.

**Request Body:**

| Field | Required | Description |
|---|---|---|
| `user_query` | Yes | The student's question |
| `context_data` | Yes | Metadata (courses, level, scores) |
| `google_api_key` | Yes | Gemini API Key |
| `format` | No | "plain" or "markdown" |

### 3. `POST /webhook/assessment-completed`

**What it does:** Simulates a Kafka trigger to log quiz results to a Google Sheet.

### 4. `POST /webhook/save-generated-note`

**What it does:** Simulates a Kafka trigger to save AI-generated notes as a Google Doc in the user's Drive.

### 5. `GET /models/discover`

**What it does:** Dynamically lists all Gemini models available to your API key and identifies the best candidates for "Pro" and "Flash" tasks.

## Testing with Swagger UI

With the server running, open your browser and navigate to:
[http://127.0.0.1:8002/docs](http://127.0.0.1:8002/docs)

## Example Test Inputs

**Test 1 -- Authenticate**

Navigate to `http://localhost:8002/login`. Once you see the success message, verify `token.json` exists.

**Test 2 -- Contextual Markdown Chat**

`POST /chat/contextual` with:

```json
{
  "user_query": "Give me a study plan for CSC332",
  "context_data": {"course": "CSC332", "score": 88},
  "google_api_key": "YOUR_KEY",
  "format": "markdown"
}
```

Expected: AI provides a structured Markdown response acknowledging your current score.

## Understanding the Responses

*   **The `\n\n` symbols in the JSON**
    These are newline escape sequences. They ensure that paragraph spacing is preserved when the data is sent between the server and your dashboard. Your frontend will render these as actual line breaks.

*   **Dynamic Model Discovery**
    The system queries Google's API at runtime to find the latest models. This ensures that if `gemini-1.5-pro` is upgraded to a newer version, AkadVerse will automatically switch to the better model without a code update.

*   **The `[KAFKA MOCK]` line**
    Every successful webhook call logs a mock Kafka event. In production, these events notify the Insight Engine to recalculate the student's performance trajectory.

## Generated Files

| File / Folder        | What it is              | Gitignore?              |
| :------------------- | :---------------------- | :---------------------- |
| `client_secret.json` | Google App Credentials  | Yes -- DO NOT COMMIT    |
| `token.json`         | Active User Token       | Yes -- DO NOT COMMIT    |
| `akadverse_audio.db` | Local Job DB            | Yes                     |

## Common Errors and Fixes

* verifier**
    This happens if you restart the server between the `/login` and `/callback` steps. The session memory is cleared. Simply run `/login` again to refresh the session.

*   **OAuth 2 MUST utilize https**
    Ensure `os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'` is set at the top of your `main.py` for local testing.

## Project Structure

```
akadverse-google-workspace-service/
|-- main.py                # Service logic, Registry, & Webhooks
|-- drive_handler.py       # Drive & Docs orchestration
|-- sheets_handler.py      # Sheets logging manager
|-- requirements.txt       # Dependencies
|-- .gitignore             # Security guardrails
```

## Part of the AkadVerse Platform

This microservice is Tier 4 in the AkadVerse AI architecture, coordinating with:

*   YouTube Recommender (Port 8000)
*   Marketplace Recommender (Port 8001)
*   Resource Tracker (Port 8003)

---

AkadVerse AI Architecture -- v1.0