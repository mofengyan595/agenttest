"""Persistent web intake; uploaded documents never create execution authority."""

import hashlib
import json
import os
import re
import sqlite3
import time
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
from threading import RLock
from uuid import uuid4

from pydantic import Field

from agenttest.contracts import load_openapi
from agenttest.credentials import CredentialVault
from agenttest.generic_intake import GenericSetup, build_generic_project
from agenttest.onboarding import _public_metadata
from agenttest.project import _read
from agenttest.project_goals import RegisteredProject, load_goal_project
from agenttest.report import fingerprint
from agenttest.report_files import pinned_directory
from agenttest.safety import StrictModel
from agenttest.task_store import TaskConflict


def clarification_question(project, text):
    """Explain the unresolved literal fragment without changing recognition or expectations."""
    from agenttest.goal_derivation import FILLER, PROJECT_PHRASES, _reference_text

    remaining = _reference_text(re.sub(r'“[^“”]+”|"[^"\n]+"', "", text))
    vocabulary = [*FILLER, *PROJECT_PHRASES, *project.catalog.resource_aliases,
                  *[a for goal in project.catalog.goals for a in goal.aliases],
                  *[o.operation_id for o in project.contract.operations if o.operation_id],
                  *[o.method + " " + o.path for o in project.contract.operations]]
    for word in sorted(vocabulary, key=len, reverse=True):
        remaining = remaining.replace(_reference_text(word), "")
    remaining = re.sub(r"[\s，。；、：:;(),!?！？]+", " ", remaining).strip()[:100]
    part = f"“{remaining}”" if remaining else "本次需求中的新业务预期或接口目标"
    supported = "、".join(g.aliases[0] for g in project.catalog.goals)
    return (f"当前已确认规则无法核验{part}。请补充对应的接口行为、适用身份和明确预期；"
            f"尚未批准的规则需先由操作员配置。此项目已支持：{supported}。未因此发送业务请求。")


class IntakeInput(StrictModel):
    name: str = Field(min_length=1, max_length=80)
    document: str = Field(default="", max_length=1_000_000)
    business_notes: str | None = Field(default=None, max_length=2000)
    profile_id: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_-]{0,39}$")
    profile_identity: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    confirmed: bool = False
    version: int = Field(default=0, ge=0)
    generic: GenericSetup | None = None


class ProjectIntake:
    """Server-fixed SQLite namespace, optimistic revisions and explicit confirmation receipts."""

    def __init__(self, service):
        self.service = service
        self._profiles = {}
        self._synthetic_environments = {}
        self._documents = {}
        self._runtime_projects = {}
        self._generic_inspections = {}
        self._cache_lock = RLock()
        self.path = service.db.path.parent / "project-intake.sqlite3"
        with pinned_directory(self.path.parent.absolute(), ".", create=True) as directory:
            try:
                os.close(directory.open(self.path.name, create=True))
            except FileExistsError:
                os.close(directory.open(self.path.name, readonly=True))
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS projects "
                       "(id TEXT PRIMARY KEY, payload TEXT NOT NULL)")

    @contextmanager
    def connect(self):
        with pinned_directory(self.path.parent.absolute(), ".", create=False) as directory:
            directory.inspect(self.path.name)
            db = sqlite3.connect(self.path, timeout=5)
            try:
                with db:
                    yield db
            finally:
                db.close()

    def records(self):
        with self.connect() as db:
            return [json.loads(row[0]) for row in db.execute(
                "SELECT payload FROM projects ORDER BY rowid DESC LIMIT 100")]

    def _cached(self, cache, key):
        with self._cache_lock:
            return cache.get(key)

    def _remember(self, cache, key, value, limit=None):
        # Parsing and compilation stay outside the lock. Published values are detached
        # and never mutated; concurrent readers still validate their own content digest.
        with self._cache_lock:
            if limit and key not in cache and len(cache) >= limit:
                cache.pop(next(iter(cache)))
            cache[key] = value

    def document(self, raw):
        """Cache parsing by actual bytes, retaining all original validation and size limits."""
        encoded = raw.encode("utf-8")
        digest = hashlib.sha256(encoded).hexdigest()
        parsed = self._cached(self._documents, digest)
        if parsed is None:
            parsed = load_openapi(encoded)
            self._remember(self._documents, digest, parsed, 16)
        return parsed.model_copy(deep=True)

    def record(self, key):
        with self.connect() as db:
            row = db.execute("SELECT payload FROM projects WHERE id=?", (key,)).fetchone()
        if row is None:
            raise KeyError(key)
        return json.loads(row[0])

    def profile(self, key):
        path = self.service.projects[key].absolute()
        raw = _read(path, 16_000)
        registration = RegisteredProject.model_validate_json(raw)
        parts = [raw, *[_read(path.parent / name) for name in (
            registration.contract, registration.target, registration.policies,
            registration.goals, registration.capabilities) if name]]
        digest = fingerprint([fingerprint(p.hex()) for p in parts])
        cached = self._cached(self._profiles, key)
        if cached is None or cached[0] != digest:
            cached = (digest, load_goal_project(path))
            self._remember(self._profiles, key, cached)
        return deepcopy(cached[1])

    def describe(self, key, project=None):
        project = project or self.profile(key)
        env = self.service.environ
        if self.service.synthetic:
            from agenttest.goal_cli import synthetic_runtime

            identity = project.identity()
            env = self._cached(self._synthetic_environments, identity)
            if env is None:
                env = synthetic_runtime(project)[0]
                self._remember(self._synthetic_environments, identity, env)
        identities = []
        for alias, role in project.target.roles.items():
            names = ([role.username_env, role.password_env] if role.login_operation
                     else [role.bearer_env])
            ready = all(n and env.get(n) and not any(c in env[n] for c in "\r\n") for n in names)
            identities.append(dict(alias=alias, ready=ready,
                instruction=("服务端合成测试身份" if self.service.synthetic else
                    "请在启动服务的环境变量中配置：" + "、".join(n for n in names if n))))
        rules = []
        for goal in project.catalog.goals:
            for policy_id in [goal.policy_id, *[v.policy_id for v in goal.variants]]:
                policy = next(p for p in project.registry.definitions() if p.id == policy_id)
                op = next(o for o in project.contract.operations
                          if o.operation_id == policy.operation_id)
                try:
                    project.target.permit(op.operation_id, op.method)
                    permitted = True
                except ValueError:
                    permitted = False
                after = next((v.after_goal for v in goal.variants
                              if v.policy_id == policy_id), None)
                condition = next((g.aliases[0] + "后" for g in project.catalog.goals
                                  if g.id == after), "")
                rules.append(dict(id=policy_id, behavior=condition + goal.aliases[0],
                    role=goal.role,
                    operation=policy.operation_id, permitted=permitted,
                    statuses=policy.assertions.status.codes if policy.assertions.status else [],
                    fields=[dict(field=f.select, operator=f.operator, expected=f.expected)
                            for f in policy.assertions.fields],
                    source=policy.assertions.source))
        result = dict(id=key, identity=project.identity(),
            name=" / ".join(project.catalog.resource_aliases),
            environment=("内存模拟目标" if self.service.synthetic else "授权测试环境"),
            address=None if self.service.synthetic else project.target.base_url,
            identities=identities, rules=rules,
            examples=["测试" + project.catalog.goals[0].aliases[0]
                      + project.catalog.resource_aliases[0] + "接口",
                      "测试" + project.catalog.resource_aliases[0] + "项目核心接口"],
            require_review=project.registration.require_plan_review)
        if project.target.business_scenario in {"web_contract_v1", "web_contract_v2"}:
            result["examples"] = ["测试" + project.catalog.goals[0].aliases[0] + "接口",
                                  "测试这个项目已授权的核心接口"]
        if project.target.business_scenario == "web_contract_v2":
            result["examples"] = ["先安排一轮常规检查，说明还有哪些没有覆盖",
                                  "帮我检查参数边界和已确认的身份访问规则"]
        # TargetConfig has already validated the trusted address; generic public prose
        # validation deliberately forbids IP literals in untrusted document text.
        _public_metadata({k: v for k, v in result.items() if k != "address"})
        vault = CredentialVault(project.target, self.service.environ)
        try:
            if vault.contains_secret(result):
                raise ValueError("PROJECT_PUBLIC_METADATA_REJECTED")
        finally:
            vault.close()
        return result

    def profiles(self):
        result = []
        for key in sorted(self.service.projects):
            try:
                result.append(self.describe(key))
            except (ValueError, OSError, KeyError):
                # A broken server profile must not hide unrelated projects or saved drafts.
                continue
        return result

    def inspect(self, record):
        if record.get("generic") is not None:
            return self.inspect_generic(record)
        missing, profile = [], None
        if not record.get("profile_id"):
            missing.append("尚未选择服务端授权配置。请让操作员登记测试环境、身份和业务预期，再重新检查。")
        else:
            try:
                profile = self.describe(record["profile_id"])
                project = self.profile(record["profile_id"])
                document = self.document(record["document"]).model_dump()
                if document != project.contract.model_dump():
                    missing.append("文档与授权配置不匹配。当前仅支持已有引擎配置；请由操作员核对接口与业务映射。")
                if record.get("confirmed_identity") != profile["identity"]:
                    missing.append("请核对并确认当前版本的业务行为和预期；文档解析不会自动批准规则。")
                for identity in profile["identities"]:
                    if not identity["ready"]:
                        missing.append("身份 " + identity["alias"] + " 缺少账号配置。"
                                       + identity["instruction"])
            except (ValueError, OSError, KeyError):
                missing.append("服务端授权配置不可用，请让操作员修正配置后重新检查。")
        state = "READY" if not missing else "NEEDS_INPUT" if record.get("profile_id") else "DRAFT"
        return dict(id=record["id"], name=record["name"], version=record["version"],
            business_notes=record.get("business_notes", ""),
            state=state, ready=not missing,
            missing=missing, profile=profile, profile_id=record.get("profile_id"),
            confirmation=record.get("confirmation"),
            identity=fingerprint(dict(record=record,
                profile=profile["identity"] if profile else None)))

    def list(self):
        return [self.inspect(r) for r in self.records()]

    def registered(self):
        result = []
        if self.service.expose_registered:
            for key in sorted(self.service.projects):
                try:
                    profile = self.describe(key)
                except (ValueError, OSError, KeyError):
                    result.append(dict(id=key, name=key, identity="", ready=False,
                        missing=["服务端授权配置不可用；请操作员检查登记文件后重新检查。"],
                        profile=None, server_registered=True))
                    continue
                missing = [i["alias"] + " 缺少账号配置。" + i["instruction"]
                           for i in profile["identities"] if not i["ready"]]
                result.append(dict(id=key, name=profile["name"], identity=profile["identity"],
                    profile=profile, ready=not missing, missing=missing, server_registered=True))
        return result

    def save(self, payload, owner, key=None):
        if self.service.private_input_guard(payload.model_dump(mode="json")):
            raise ValueError("PROJECT_CREDENTIAL_LITERAL")
        previous = self.record(key) if key else None
        if previous and payload.version != previous["version"]:
            raise TaskConflict("PROJECT_VERSION_CHANGED")
        if not previous and payload.version:
            raise TaskConflict("PROJECT_VERSION_CHANGED")
        document = payload.document or (previous["document"] if previous else "")
        notes = (payload.business_notes if payload.business_notes is not None
                 else (previous or {}).get("business_notes", "")).strip()
        if self.service.private_input_guard([payload.name, document, notes]):
            raise ValueError("PROJECT_CREDENTIAL_LITERAL")
        self.document(document)
        _public_metadata([payload.name, notes])
        generic = payload.generic
        if generic is None and previous and previous.get("generic") and not payload.profile_id:
            generic = GenericSetup.model_validate(previous["generic"])
        if generic is not None and (not self.service.generic_intake or payload.profile_id):
            raise ValueError("GENERIC_INTAKE_NOT_ENABLED")
        if generic is not None:
            from agenttest.project_inventory import credential_field, input_fields

            contract = self.document(document)
            operations = {o.operation_id: o for o in contract.operations}
            for item in generic.operations:
                try:
                    secret_slots = {(s.location, s.name) for s in input_fields(
                        operations[item.operation_id], contract.version)
                        if credential_field(s)}
                except (ValueError, KeyError):
                    secret_slots = set()
                if any(a.value is not None and ((a.location, a.name) in secret_slots or re.search(
                        r"password|passwd|^pwd$|token|api.?key|authorization|cookie", a.name, re.I))
                        for a in item.arguments):
                    raise ValueError("PROJECT_CREDENTIAL_LITERAL")
                if any(a.location == "body" and a.name in item.credential_inputs
                       and (a.value is not None or a.source_operation or a.generated
                            or a.negative_enabled)
                       for a in item.arguments):
                    raise ValueError("PROJECT_CREDENTIAL_LITERAL")
            public_data = json.dumps([payload.name, document, notes, generic.model_dump()],
                                     ensure_ascii=False)
            if self.service.private_input_guard(public_data):
                raise ValueError("PROJECT_CREDENTIAL_LITERAL")
            # Reject known target secrets even when saving an unconfirmed draft.
            for name, value in self.service.environ.items():
                if (name.startswith("AGENTTEST_TARGET_") and value
                        and (any(part in name for part in (
                            "TOKEN", "BEARER", "PASSWORD", "SECRET"))
                            or any(name in item.credential_inputs.values()
                                   for item in generic.operations))
                        and value in public_data):
                    raise ValueError("PROJECT_CREDENTIAL_LITERAL")
        for profile_key in self.service.projects:
            project = self.profile(profile_key)
            vault = CredentialVault(project.target, self.service.environ)
            try:
                if vault.contains_secret([payload.name, document, notes]):
                    raise ValueError("PROJECT_CREDENTIAL_LITERAL")
            finally:
                vault.close()
        confirmed_identity, confirmation = None, None
        if payload.profile_id:
            profile = self.describe(payload.profile_id)
            if payload.confirmed:
                if payload.profile_identity != profile["identity"]:
                    raise TaskConflict("PROJECT_AUTHORIZATION_CHANGED")
                confirmed_identity = profile["identity"]
                confirmation = dict(source="WEB_OPERATOR_SESSION", actor=owner,
                    configuration_version=confirmed_identity, at=time.time())
        record = dict(id=key or "web_" + uuid4().hex[:24], name=payload.name.strip(),
            document=document, profile_id=payload.profile_id, version=payload.version + 1,
            confirmed_identity=confirmed_identity, confirmation=confirmation)
        if notes:
            record["business_notes"] = notes
        if generic is not None:
            record["generic"] = generic.model_dump()
            if payload.confirmed:
                from agenttest.safety import RoleCredential, TargetConfig

                # Always check EVERY configured identity, including blocked/unused operations.
                # This privacy check runs before either assisted or manual confirmation persists.
                privacy_target = TargetConfig(base_url=generic.base_url, environment="isolated",
                    authorized_ips=list(self.service.intake_authorized_ips),
                    allow_mutations=generic.allow_mutations, allowed_operations=["intake_only"],
                    roles={r.alias: RoleCredential.model_validate(r.model_dump(exclude={"alias"}))
                           for r in generic.roles})
                vault = CredentialVault(privacy_target, self.service.environ)
                try:
                    if vault.contains_secret([document, record["name"], notes, record["generic"]]):
                        raise ValueError("PROJECT_CREDENTIAL_LITERAL")
                finally:
                    vault.close()
                _public_metadata([o.alias for o in generic.operations])
                if generic.assisted:
                    if not generic.isolated_confirmed:
                        raise ValueError("ISOLATED_ENVIRONMENT_CONFIRMATION_REQUIRED")
                    record["confirmed_setup_sha256"] = fingerprint(generic.model_dump())
                    record["confirmation"] = dict(source="WEB_OPERATOR_SESSION", actor=owner,
                        configuration_version=record["confirmed_setup_sha256"], at=time.time())
                else:
                    project = build_generic_project(record["id"], document,
                        self.document(document), generic, self.service.intake_authorized_ips,
                        self.service.environ)
                    # Retain the original final-project credential check as well.
                    vault = CredentialVault(project.target, self.service.environ)
                    try:
                        if vault.contains_secret([document, record["name"], notes,
                                                  record["generic"]]):
                            raise ValueError("PROJECT_CREDENTIAL_LITERAL")
                    finally:
                        vault.close()
                    record["confirmed_identity"] = project.identity()
                    record["confirmation"] = dict(source="WEB_OPERATOR_SESSION", actor=owner,
                        configuration_version=project.identity(), at=time.time())
        if not record["name"]:
            raise ValueError("PROJECT_NAME_REQUIRED")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if previous:
                current = db.execute("SELECT payload FROM projects WHERE id=?", (key,)).fetchone()
                if current is None or json.loads(current[0])["version"] != payload.version:
                    raise TaskConflict("PROJECT_VERSION_CHANGED")
                db.execute("UPDATE projects SET payload=? WHERE id=?",
                           (json.dumps(record, ensure_ascii=False), key))
            else:
                if db.execute("SELECT COUNT(*) FROM projects").fetchone()[0] >= 100:
                    raise TaskConflict("PROJECT_LIMIT_REACHED")
                db.execute("INSERT INTO projects VALUES (?,?)",
                           (record["id"], json.dumps(record, ensure_ascii=False)))
        return self.inspect(record)

    def project(self, key):
        record = self.record(key)
        runtime_key = None
        if record.get("generic") is not None:
            config = GenericSetup.model_validate(record["generic"])
            credential_names = {name for role in config.roles
                for name in (role.username_env, role.password_env, role.bearer_env) if name}
            credential_names.update(name for item in config.operations
                                    for name in item.credential_inputs.values())
            # Memory-only digest: changes to credentials, authorization or any record field
            # invalidate the compiled runtime before the next guarded HTTP request.
            runtime_key = fingerprint(dict(record=record,
                enabled=self.service.generic_intake, ips=self.service.intake_authorized_ips,
                credentials={n: self.service.environ.get(n) for n in credential_names}))
            cached = self._cached(self._runtime_projects, runtime_key)
            if cached is not None:
                return deepcopy(cached)
        if not self.inspect(record)["ready"]:
            raise ValueError("PROJECT_INTAKE_NOT_READY")
        project = (self.generic_project(record) if record.get("generic") is not None
                   else self.profile(record["profile_id"]))
        registration = project.registration.model_copy(update={"id": key})
        # Revision binds in-flight tasks even when only display metadata changes.
        project = replace(project, registration=registration, document=record["document"].encode(),
                          intake_revision=runtime_key or fingerprint(record))
        if runtime_key:
            self._remember(self._runtime_projects, runtime_key, deepcopy(project), 16)
        return project

    def generic_project(self, record):
        if not self.service.generic_intake:
            raise ValueError("GENERIC_INTAKE_NOT_ENABLED")
        return build_generic_project(record["id"], record["document"],
            self.document(record["document"]), GenericSetup.model_validate(record["generic"]),
            self.service.intake_authorized_ips, self.service.environ)

    def answer_permission(self, task_id, owner, question_id, status, version):
        """A concrete operator answer, never a model-authored permission grant."""
        from agenttest.generic_intake import IntakeAccess

        task = self.service.db.get(task_id, owner)
        record = self.record(task["project_id"])
        current = self.project(task["project_id"])
        if record["version"] != version or current.identity() != task["project_identity"]:
            raise TaskConflict("QUESTION_PROJECT_VERSION_CHANGED")
        if task["summary"].get("mode") != "MODEL_DESIGNED":
            raise ValueError("PERMISSION_QUESTION_REQUIRED")
        question = next((q for q in task["summary"].get("questions", [])
                         if q["id"] == question_id), None)
        if not question or question.get("kind") != "permission" or status not in {403, 404}:
            raise ValueError("PERMISSION_QUESTION_REQUIRED")
        if status not in question.get("status_choices", []):
            raise ValueError("PERMISSION_ANSWER_NOT_OFFERED")
        config = GenericSetup.model_validate(record["generic"])
        operation = next((o for o in config.operations
                          if o.operation_id == question["operation_id"]), None)
        op = next((o for o in current.contract.operations
                   if o.operation_id == question["operation_id"]), None)
        if (operation is None or op is None or op.method not in {"GET", "HEAD", "OPTIONS"}
                or question.get("role") not in {r.alias for r in config.roles}):
            raise ValueError("PERMISSION_ANSWER_SCOPE_INVALID")
        rule = IntakeAccess(role=question["role"], statuses=[status],
                            rule=question["requested_behavior"] + f"；已确认返回 {status}",
                            source_question=question_id)
        if any(a.role == rule.role for a in operation.access_checks):
            raise TaskConflict("ACCESS_RULE_ALREADY_CONFIGURED_REVIEW_IN_PROJECT")
        operation.access_checks.append(rule)
        return self.save(IntakeInput(name=record["name"], version=version, generic=config,
                                    confirmed=True), owner, record["id"])

    def inspect_generic(self, record):
        """Reuse public readiness only while the full record and authorization remain identical."""
        key = fingerprint(dict(record=record, enabled=self.service.generic_intake,
            ips=self.service.intake_authorized_ips, environ=self.service.environ))
        previous = self._cached(self._generic_inspections, record["id"])
        if previous is not None and previous[0] == key:
            return deepcopy(previous[1])
        result = self._inspect_generic(record)
        self._remember(self._generic_inspections, record["id"], (key, deepcopy(result)), 100)
        return result

    def _inspect_generic(self, record):
        from agenttest.project_inventory import inventory

        missing, profile = [], None
        setup = GenericSetup.model_validate(record["generic"])
        complete_inventory = inventory(self.document(record["document"]), setup,
                                       self.service.environ)
        try:
            project = self.generic_project(record)
            profile = self.describe(record["id"], project)
            confirmed = (record.get("confirmed_setup_sha256") in {
                fingerprint(setup.model_dump()), fingerprint(record["generic"])}
                if setup.assisted else record.get("confirmed_identity") == project.identity())
            if not confirmed:
                missing.append("请核对并确认所选接口、输入、预期及隔离环境授权。")
            for identity in profile["identities"]:
                if not identity["ready"]:
                    missing.append("身份 " + identity["alias"] + " 缺少账号配置。"
                                   + identity["instruction"])
        except (ValueError, OSError, KeyError):
            missing.extend(dict.fromkeys(e["reason"] + " " + e["next_step"]
                for e in complete_inventory["operations"] if e["state"] != "READY"))
            if not missing:
                missing.append("接口配置校验失败，请检查资源依赖是否循环、输入与状态是否符合文档。")
        return dict(id=record["id"], name=record["name"], version=record["version"],
            business_notes=record.get("business_notes", ""),
            state="READY" if not missing else "NEEDS_INPUT", ready=not missing,
            missing=missing, profile=profile, profile_id=None, generic=record["generic"],
            inventory=complete_inventory,
            testing_mode="MODEL_DESIGNED" if setup.assisted else "REGISTERED_GOALS",
            confirmation=record.get("confirmation"),
            identity=fingerprint(dict(record=record,
                profile=profile["identity"] if profile else None)))
