"""Campus Connect demo: real sample files that are analyzed by the normal pipeline."""
FILES = {
"README.md": "# Campus Connect\nA campus events and student portal.\n",
"requirements.txt": "fastapi\nuvicorn\nsqlalchemy\n",
"app/main.py": '''from fastapi import FastAPI, Depends
app = FastAPI()

@app.post("/api/register")
def register_user(name, email, password):
    """Registers a new student account."""
    return {}

@app.post("/api/login")
def login_user(email, password):
    """Authenticates a user."""
    return {"token": "x"}

@app.get("/api/events")
def list_events(category=None):
    """Lists campus events."""
    return []

@app.post("/api/events")
def create_event(title, date, user=Depends(lambda: 1)):
    """Creates a new event."""
    return {}

@app.post("/api/password-reset")
def password_reset(email):
    return {}
''',
"app/services.py": '''class EventService:
    """Business logic for events."""
    def register_attendee(self, event_id, user_id):
        """Adds an attendee to an event."""
        return True
    def cancel_event(self, event_id):
        return True

def hash_password(password):
    """Hashes a password."""
    return password
def send_email(to, body):
    return None
''',
"db/schema.sql": '''-- Registered students and staff
CREATE TABLE users (id INTEGER PRIMARY KEY, name VARCHAR(100) NOT NULL, email VARCHAR(120) NOT NULL UNIQUE, password VARCHAR(255) NOT NULL, created_at TIMESTAMP);
-- Campus events
CREATE TABLE events (id INTEGER PRIMARY KEY, title VARCHAR(150) NOT NULL, event_date DATE, organizer_id INTEGER, FOREIGN KEY (organizer_id) REFERENCES users(id));
CREATE TABLE attendees (id INTEGER PRIMARY KEY, event_id INTEGER REFERENCES events(id), user_id INTEGER REFERENCES users(id));
''',
"tests/test_auth.py": '''def test_login():
    assert True
''',
}
REQS = [("User Registration", "Users should be able to register an account.", "Functional", "High"),
        ("User Login", "Users should be able to log into the system.", "Functional", "Critical"),
        ("Event Listing", "Users should be able to view a list of campus events.", "Functional", "Medium"),
        ("Password Reset", "Users should receive a password reset email.", "Functional", "Medium"),
        ("Response Time", "The system should respond to requests within 2 seconds.", "Non-functional", "Low")]
TICKETS = [("TCK-102", "Add Password Reset", "Users should receive a password reset email.", "Feature", "High")]
