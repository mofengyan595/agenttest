"""Independent disposable FastAPI target with two explicit defect switches."""

import csv
import io
import os
import secrets
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

Fault = Literal["none", "ownership", "terminal"]


class LoginInput(BaseModel):
    username: str
    password: str


class CourseInput(BaseModel):
    name: str


def create_target(users: dict[str, str], fault: Fault = "none") -> FastAPI:
    """All resource state belongs to this app; faults affect only this disposable target."""
    if fault not in {"none", "ownership", "terminal"}:
        raise ValueError("Unknown demo fault")
    app = FastAPI(title="AgentTest disposable mini target", version="1.0")
    tokens: dict[str, str] = {}
    courses: dict[int, dict] = {}
    batches: dict[int, dict] = {}
    bearer = HTTPBearer(auto_error=False)

    def current_user(
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    ) -> str:
        if credentials is None or credentials.credentials not in tokens:
            raise HTTPException(401, "Authentication required")
        return tokens[credentials.credentials]

    user_dependency = Annotated[str, Depends(current_user)]

    def owned_course(course_id: int, user: str) -> dict:
        if course_id not in courses:
            raise HTTPException(404, "Unknown course")
        course = courses[course_id]
        if fault != "ownership" and course["owner"] != user:
            raise HTTPException(403, "Forbidden")
        return course

    @app.post("/api/auth/login", operation_id="login")
    def login(payload: LoginInput) -> dict:
        if payload.username not in users or not secrets.compare_digest(
            users[payload.username], payload.password,
        ):
            raise HTTPException(401, "Invalid credentials")
        token = secrets.token_urlsafe(32)
        tokens[token] = payload.username
        return {"access_token": token}

    @app.post("/api/courses", operation_id="createCourse", status_code=201)
    def create_course(payload: CourseInput, user: user_dependency) -> dict:
        course_id = len(courses) + 1
        courses[course_id] = {"courseId": course_id, "owner": user, "name": payload.name}
        return courses[course_id]

    @app.get("/api/courses/{courseId}", operation_id="getCourse")
    def get_course(courseId: int, user: user_dependency) -> dict:
        return owned_course(courseId, user)

    @app.post("/api/courses/{courseId}/analysis-batches", operation_id="createBatch",
              status_code=202)
    async def create_batch(
        courseId: int, user: user_dependency, file: UploadFile = File(...),
    ) -> dict:
        owned_course(courseId, user)
        raw = await file.read(100_001)
        if len(raw) > 100_000:
            raise HTTPException(413, "CSV too large")
        try:
            reader = csv.DictReader(io.StringIO(raw.decode("utf-8")))
            rows = list(reader)
            if reader.fieldnames != ["text"] or not rows or any(not r.get("text") for r in rows):
                raise ValueError("Invalid CSV")
        except (UnicodeError, ValueError, csv.Error) as exc:
            raise HTTPException(422, "Expected CSV with text column") from exc
        batch_id = len(batches) + 1
        batches[batch_id] = {"batchId": batch_id, "courseId": courseId,
                             "owner": user, "count": len(rows), "polls": 0}
        return {"batchId": batch_id, "state": "QUEUED"}

    def owned_batch(batch_id: int, user: str) -> dict:
        if batch_id not in batches:
            raise HTTPException(404, "Unknown batch")
        batch = batches[batch_id]
        owned_course(batch["courseId"], user)
        return batch

    @app.get("/api/analysis-batches/{batchId}", operation_id="getBatch")
    def get_batch(batchId: int, user: user_dependency) -> dict:
        batch = owned_batch(batchId, user)
        batch["polls"] += 1
        state = "RUNNING" if batch["polls"] < 2 else (
            "FAILED" if fault == "terminal" else "SUCCEEDED"
        )
        return {"batchId": batchId, "state": state, "count": batch["count"]}

    @app.get("/api/analysis-batches/{batchId}/results", operation_id="getBatchResults")
    def get_results(batchId: int, user: user_dependency) -> dict:
        batch = owned_batch(batchId, user)
        if batch["polls"] < 2 or fault == "terminal":
            raise HTTPException(409, "Batch is not successful")
        return {"items": [{"row": n + 1} for n in range(batch["count"])]}

    return app


def local_app() -> FastAPI:
    """Factory for uvicorn; credentials are mandatory environment variables."""
    users = {
        os.environ["DEMO_ALICE_USERNAME"]: os.environ["DEMO_ALICE_PASSWORD"],
        os.environ["DEMO_BOB_USERNAME"]: os.environ["DEMO_BOB_PASSWORD"],
    }
    return create_target(users)
