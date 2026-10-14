"""Independent disposable ticket service; no course routes or shared target state."""

from typing import Annotated, Literal

from fastapi import Depends, FastAPI, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field

TicketFault = Literal["none", "owner_leak", "wrong_subject", "close_ignored"]


class TicketInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    subject: str = Field(min_length=1, max_length=80)
    priority: Literal["low", "high"]


class TicketUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    state: str = Field(min_length=1, max_length=20)


def create_ticket_target(accounts: dict[str, str], fault: TicketFault = "none") -> FastAPI:
    """Each instance owns its in-memory tickets and explicit synthetic bearer accounts."""
    if fault not in {"none", "owner_leak", "wrong_subject", "close_ignored"}:
        raise ValueError("Unknown ticket fault")
    if not accounts or len(set(accounts.values())) != len(accounts):
        raise ValueError("Distinct synthetic credentials required")
    accounts = dict(accounts)
    app = FastAPI(title="AgentTest disposable ticket service", version="1.0")
    tickets: dict[str, dict] = {}
    bearer = HTTPBearer(auto_error=False)

    def actor(credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]):
        if credentials is not None:
            for name, token in accounts.items():
                if credentials.credentials == token:
                    return name
        raise HTTPException(401, "Authentication required")

    principal = Annotated[str, Depends(actor)]

    def owned(key: str, user: str) -> dict:
        if key not in tickets:
            raise HTTPException(404, "Ticket not found")
        if fault != "owner_leak" and tickets[key]["owner"] != user:
            raise HTTPException(403, "Ticket belongs to another account")
        return tickets[key]

    def public(ticket: dict) -> dict:
        return {k: v for k, v in ticket.items() if k != "owner"}

    @app.post("/v2/tickets", operation_id="openTicket", status_code=201)
    def open_ticket(body: TicketInput, user: principal) -> dict:
        key = f"TK-{len(tickets) + 1}"
        ticket = {"ticket_key": key, "subject": body.subject, "priority": body.priority,
                  "state": "open", "owner": user}
        tickets[key] = ticket
        return {"payload": public(ticket)}

    @app.get("/v2/tickets", operation_id="listTickets")
    def list_tickets(user: principal) -> dict:
        return {"payload": {"items": [public(t) for t in tickets.values() if t["owner"] == user]}}

    @app.get("/v2/tickets/{ticket_key}", operation_id="readTicket")
    def read_ticket(ticket_key: str, user: principal) -> dict:
        ticket = public(owned(ticket_key, user))
        if fault == "wrong_subject":
            ticket["subject"] = "Injected inconsistent subject"
        return {"payload": ticket}

    @app.patch("/v2/tickets/{ticket_key}", operation_id="closeTicket")
    def close_ticket(ticket_key: str, body: TicketUpdate, user: principal) -> dict:
        ticket = owned(ticket_key, user)
        if body.state != "closed":
            raise HTTPException(422, "Only closing a ticket is supported")
        if fault != "close_ignored":
            ticket["state"] = body.state
        return {"payload": public(ticket)}

    return app
