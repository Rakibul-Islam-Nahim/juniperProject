"""Events package — persistence helper for state transitions and log events."""
from app.events.service import record_event, transition_status

__all__ = ["record_event", "transition_status"]
