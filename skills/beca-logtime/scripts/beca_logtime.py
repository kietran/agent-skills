#!/usr/bin/env python3
from __future__ import annotations

import argparse
import getpass
import html.parser
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from http.cookiejar import CookieJar
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlencode, urljoin, urlparse
from urllib.request import HTTPCookieProcessor, Request, build_opener
from zoneinfo import ZoneInfo


WORK_ORIGIN = "https://work.becawork.vn"
SSO_ORIGIN = "https://sso.becawork.vn"
USER_AGENT = "Mozilla/5.0"
LOCAL_TIMEZONE = ZoneInfo("Asia/Ho_Chi_Minh")
LOGIN_URL = (
    "https://sso.becawork.vn/Account/Login?"
    "ReturnUrl=%2Fconnect%2Fauthorize%2Fcallback%3Fclient_id%3Dvwork.work"
    "%26redirect_uri%3Dhttps%253A%252F%252Fwork.becawork.vn%252Fsignin-oidc"
    "%26response_type%3Dcode%2520id_token"
    "%26scope%3DEOfficeAPI.read%2520openid%2520profile%2520email"
    "%26response_mode%3Dform_post"
)


class BecaError(RuntimeError):
    pass


class FormParser(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.action = ""
        self.inputs: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "form" and not self.action:
            self.action = values.get("action") or ""
            return
        if tag == "input":
            name = values.get("name")
            if name:
                self.inputs[name] = values.get("value") or ""


@dataclass
class PreparedLogtime:
    form_id: str
    step_id: str
    save_url: str
    payload: dict[str, Any]
    user: dict[str, Any]
    over_logtime: Any
    duplicates: list[dict[str, Any]]


@dataclass
class PreparedLogtimeUpdate:
    form_id: str
    update_url: str
    payload: dict[str, Any]
    user: dict[str, Any]
    existing: dict[str, Any]
    log_user_workflow_id: str
    over_logtime: Any
    duplicates: list[dict[str, Any]]
    old_value_for_validation: str


class BecaClient:
    def __init__(self, cookie: str | None = None) -> None:
        self.cookie = cookie
        self.opener = build_opener(HTTPCookieProcessor(CookieJar()))
        self._xsrf_token: str | None = None

    def ensure_auth(self) -> None:
        if self.cookie:
            return
        username = os.getenv("BECA_USERNAME") or input("BecaWork username: ")
        password = os.getenv("BECA_PASSWORD") or getpass.getpass("BecaWork password: ")
        self.login(username, password)

    def login(self, username: str, password: str) -> None:
        login_page = self._open(Request(LOGIN_URL, headers=self._headers()))
        login_html = login_page.read().decode("utf-8", errors="replace")

        parser = FormParser()
        parser.feed(login_html)
        if not parser.inputs:
            raise BecaError("Could not find login form on SSO page.")

        form_data = {
            **parser.inputs,
            "Username": username,
            "Password": password,
            "Input.Username": username,
            "Input.Password": password,
            "RememberMe": "false",
            "button": "login",
        }
        response = self._open(
            Request(
                LOGIN_URL,
                data=urlencode(form_data).encode("utf-8"),
                headers=self._headers(
                    {
                        "Content-Type": "application/x-www-form-urlencoded",
                        "Origin": SSO_ORIGIN,
                        "Referer": LOGIN_URL,
                    }
                ),
                method="POST",
            )
        )
        body = response.read().decode("utf-8", errors="replace")
        if "<form" in body and "signin-oidc" in body:
            self._submit_html_form(body, response.geturl())

    def get_json(self, path_or_url: str, params: dict[str, Any] | None = None) -> Any:
        url = self._url(path_or_url, params)
        body = self._request_text(url, method="GET")
        return self._loads_json(body, url)

    def post_json(
        self,
        path_or_url: str,
        payload: Any,
        params: dict[str, Any] | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> Any:
        url = self._url(path_or_url, params)
        headers = {
            "Content-Type": "application/json; charset=UTF-8",
            "Origin": WORK_ORIGIN,
            "Referer": f"{WORK_ORIGIN}/work/timesheet",
            "X-XSRF-TOKEN": self.xsrf_token(),
            **(extra_headers or {}),
        }
        body = self._request_text(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        return self._loads_json(body, url)

    def put_json(
        self,
        path_or_url: str,
        payload: Any,
        params: dict[str, Any] | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> Any:
        url = self._url(path_or_url, params)
        headers = {
            "Content-Type": "application/json; charset=UTF-8",
            "Origin": WORK_ORIGIN,
            "Referer": f"{WORK_ORIGIN}/work/timesheet",
            "X-XSRF-TOKEN": self.xsrf_token(),
            **(extra_headers or {}),
        }
        body = self._request_text(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="PUT",
        )
        return self._loads_json(body, url)

    def xsrf_token(self) -> str:
        if self._xsrf_token:
            return self._xsrf_token
        token = self.get_json("/api/antiforgery/token")
        self._xsrf_token = str(token)
        return self._xsrf_token

    def _request_text(
        self,
        url: str,
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
        method: str = "GET",
    ) -> str:
        response = self._open(
            Request(url, data=data, headers=self._headers(headers), method=method)
        )
        body = response.read().decode("utf-8", errors="replace")
        if "<form" in body and "signin-oidc" in body:
            self._submit_html_form(body, response.geturl())
            response = self._open(
                Request(url, data=data, headers=self._headers(headers), method=method)
            )
            body = response.read().decode("utf-8", errors="replace")
        if "Account/Login" in response.geturl() and "<form" in body:
            raise BecaError("BecaWork session is not authenticated. Refresh BECA_COOKIE or login again.")
        return body

    def _submit_html_form(self, html: str, base_url: str) -> str:
        parser = FormParser()
        parser.feed(html)
        if not parser.action or not parser.inputs:
            raise BecaError("Could not submit authentication callback form.")
        action = urljoin(base_url, parser.action)
        response = self._open(
            Request(
                action,
                data=urlencode(parser.inputs).encode("utf-8"),
                headers=self._headers(
                    {
                        "Content-Type": "application/x-www-form-urlencoded",
                        "Origin": origin_for(action),
                        "Referer": base_url,
                    }
                ),
                method="POST",
            )
        )
        return response.read().decode("utf-8", errors="replace")

    def _open(self, request: Request):
        try:
            return self.opener.open(request, timeout=30)
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise BecaError(f"HTTP {exc.code} for {request.full_url}: {body[:500]}") from exc

    def _headers(self, headers: dict[str, str] | None = None) -> dict[str, str]:
        merged = {
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
            **(headers or {}),
        }
        if self.cookie:
            merged["Cookie"] = self.cookie
        return merged

    def _url(self, path_or_url: str, params: dict[str, Any] | None = None) -> str:
        url = path_or_url if path_or_url.startswith("http") else f"{WORK_ORIGIN}{path_or_url}"
        if params:
            url = f"{url}?{urlencode(params)}"
        return url

    @staticmethod
    def _loads_json(body: str, url: str) -> Any:
        try:
            return json.loads(body)
        except json.JSONDecodeError as exc:
            raise BecaError(f"Response from {url} is not JSON: {body[:500]}") from exc


def origin_for(url: str) -> str:
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}"


def previous_business_day(today: date | None = None) -> date:
    current = today or datetime.now(LOCAL_TIMEZONE).date()
    current -= timedelta(days=1)
    while current.weekday() >= 5:
        current -= timedelta(days=1)
    return current


def html_description(description: str) -> str:
    stripped = description.strip()
    if stripped.startswith("<"):
        return description
    return f"<p>{description}</p>"


def normalize_user_id(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        raise BecaError("Current user response did not include an id.")
    return text if text.startswith("P:") else f"P:{text}"


def normalize_date_value(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        return text[:10]
    if len(text) >= 10 and text[2] == "/" and text[5] == "/":
        day, month, year = text[:10].split("/")
        return f"{year}-{month}-{day}"
    if len(text) >= 10 and text[4] == "/" and text[7] == "/":
        return text[:10].replace("/", "-")
    return text[:10]


def number_or_none(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def api_number_text(value: Any) -> str:
    number = number_or_none(value)
    if number is not None:
        return str(int(number)) if number.is_integer() else str(number)
    return str(value or "").strip()


def parse_log_date(raw: str | None) -> date:
    if raw:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    return previous_business_day()


def now_string() -> str:
    return datetime.now(LOCAL_TIMEZONE).strftime("%Y-%m-%d %H:%M:%S")


def find_field(form_format: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
    for section in form_format:
        for field in section.get("row", []):
            if field.get("name") == name:
                return field
    return None


def default_field_value(form_format: list[dict[str, Any]], name: str) -> str | None:
    field = find_field(form_format, name)
    value = field.get("defaultValue") if field else None
    return str(value) if value is not None else None


def object_to_fields(values: dict[str, Any]) -> list[dict[str, Any]]:
    fields: list[dict[str, Any]] = []
    for name, value in values.items():
        field_value = None if value is None or value == "" else str(value)
        fields.append({"name": name, "value": field_value})
    return fields


def build_form_payload(values: dict[str, Any]) -> dict[str, Any]:
    fields = object_to_fields(values)
    return {
        "data": fields,
        "data_json": json.dumps({item["name"]: item["value"] for item in fields}, ensure_ascii=False),
        "isDraft": True,
    }


def response_data(value: Any) -> Any:
    if isinstance(value, dict) and set(value.keys()) <= {"data", "status", "message", "hasErrors"}:
        return value.get("data")
    return value


def log_row_id(row: dict[str, Any]) -> str:
    value = row.get("Id", row.get("id", ""))
    return str(value or "").strip()


def log_row_user_workflow_id(row: dict[str, Any]) -> str:
    value = row.get("UserWorkflowId", row.get("userWorkflowId", ""))
    return str(value or "").strip()


def log_row_date(row: dict[str, Any]) -> str | None:
    return normalize_date_value(row.get("Ngay") or row.get("DateLogtime") or row.get("dateLogtime"))


def parse_json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def first_value(*values: Any) -> Any:
    for value in values:
        if value is not None and value != "":
            return value
    return None


def flatten_tasks(items: list[dict[str, Any]], parent_title: str | None = None) -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = []
    for item in items:
        row = dict(item)
        if parent_title:
            row["parentTitle"] = parent_title
        tasks.append(row)
        children = item.get("children") or []
        if isinstance(children, list):
            tasks.extend(flatten_tasks(children, item.get("title") or parent_title))
    return tasks


def list_tasks(client: BecaClient, args: argparse.Namespace) -> list[dict[str, Any]]:
    params = {
        "title": args.title or "",
        "projectName": args.project_name or "",
        "group": "",
        "status": "          ",
        "employee": "",
        "type": args.type,
        "isComplete": 1,
        "layout": 2,
        "pageNumber": args.page,
        "rowNumber": args.rows,
        "projectType": "",
        "typeSort": 0,
        "overDue": -1,
    }
    data = client.get_json("/api/Default/Work_GetWorkInProcess", params)
    if not isinstance(data, list):
        raise BecaError("Unexpected Work_GetWorkInProcess response.")
    tasks = flatten_tasks(data)
    if args.mine_only:
        tasks = [task for task in tasks if task.get("isMyWork") is True]
    return tasks


def get_api_setting(client: BecaClient) -> tuple[str, str]:
    data = client.get_json("/api/Default/Work_GetApiSetting")
    if not isinstance(data, dict):
        raise BecaError("Unexpected Work_GetApiSetting response.")
    form_id = data.get("id_getWorkFormLogTime", {}).get("id") or 101
    step_id = data.get("id_getWorkFormLogTimeStep", {}).get("id") or 415
    return str(form_id), str(step_id)


def get_form_format(client: BecaClient, form_id: str) -> list[dict[str, Any]]:
    data = client.post_json(
        "/api/ApiEoffice/Eoffice_GetData",
        [
            {"Name": "id", "Value": int(form_id) if str(form_id).isdigit() else form_id},
            {"Name": "isChild"},
            {"Name": "WorkType"},
        ],
        {
            "urlStr": "getFormatWorkflow",
            "projectId": "undefined",
            "workId": "undefined",
        },
    )
    if not isinstance(data, list):
        raise BecaError("Unexpected getFormatWorkflow response.")
    return data


def duplicate_logs(
    client: BecaClient,
    work_id: str,
    log_date: date,
    exclude_log_id: str | None = None,
    exclude_user_workflow_id: str | None = None,
) -> list[dict[str, Any]]:
    data = client.get_json("/api/Default/Work_GetLogtimeByWorkId", {"WorkFlowId": work_id})
    if not isinstance(data, list):
        raise BecaError("Unexpected Work_GetLogtimeByWorkId response.")
    target = log_date.isoformat()
    excluded_log_id = str(exclude_log_id or "").strip()
    excluded_user_workflow_id = str(exclude_user_workflow_id or "").strip()
    duplicates = []
    for item in data:
        if excluded_log_id and log_row_id(item) == excluded_log_id:
            continue
        if excluded_user_workflow_id and log_row_user_workflow_id(item) == excluded_user_workflow_id:
            continue
        if log_row_date(item) == target:
            duplicates.append(item)
    return duplicates


def check_over_logtime(
    client: BecaClient,
    log_date: date,
    hours: str,
    old_hours: str = "0",
) -> Any:
    return client.get_json(
        "/api/Default/Work_CheckOverInLogtime",
        {
            "newVal": hours,
            "oldVal": old_hours,
            "day": log_date.isoformat(),
            "isCheckin": "false",
        },
    )


def prepare_logtime(client: BecaClient, args: argparse.Namespace) -> PreparedLogtime:
    log_date = parse_log_date(args.date)
    hours = api_number_text(args.hours)
    if not args.description or not args.description.strip():
        raise BecaError("--description is required.")

    form_id, step_id = get_api_setting(client)
    form_format = get_form_format(client, form_id)
    user = client.get_json("/api/Default/Work_GetInfLogin", {"IsMobile": "false"})
    if not isinstance(user, dict):
        raise BecaError("Unexpected Work_GetInfLogin response.")

    over_logtime = response_data(check_over_logtime(client, log_date, hours))
    over_logtime_number = number_or_none(over_logtime)
    if over_logtime_number is None:
        raise BecaError(f"Unexpected over-logtime validation response: {over_logtime!r}")
    if over_logtime_number < 0:
        raise BecaError(f"Over-logtime validation failed: {over_logtime}")

    duplicates = duplicate_logs(client, args.work_id, log_date)
    if duplicates and not args.allow_duplicate:
        raise BecaError(
            f"Found {len(duplicates)} existing logtime row(s) for work {args.work_id} on {log_date}."
        )

    values = {
        "Nguoilap": default_field_value(form_format, "Nguoilap") or user.get("fullName"),
        "Ngaylap": now_string(),
        "Email": default_field_value(form_format, "Email") or user.get("email"),
        "Duan": str(args.project_id).strip(),
        "Congviec": str(args.work_id).strip(),
        "Ngay": f"{log_date.isoformat()} 00:00:00",
        "SoGio": hours,
        "Hanhdong": args.action,
        "Mota": html_description(args.description),
        "UserId": normalize_user_id(user.get("id")),
    }
    payload = build_form_payload(values)
    save_url = (
        f"/api/ApiEoffice/Eoffice_ValidateAndInsertData?"
        f"{urlencode({'urlStr': f'/api/apikey/userWorkflows/{form_id}/addDynamicUserWorkflow/{step_id}', 'apiId': form_id})}"
    )
    return PreparedLogtime(
        form_id=form_id,
        step_id=step_id,
        save_url=save_url,
        payload=payload,
        user=user,
        over_logtime=over_logtime,
        duplicates=duplicates,
    )


def get_current_user(client: BecaClient) -> dict[str, Any]:
    user = client.get_json("/api/Default/Work_GetInfLogin", {"IsMobile": "false"})
    if not isinstance(user, dict):
        raise BecaError("Unexpected Work_GetInfLogin response.")
    return user


def get_logtime(client: BecaClient, log_id: str) -> dict[str, Any]:
    data = response_data(client.get_json("/api/Default/Work_GetLogtimeById", {"id": log_id}))
    if not isinstance(data, dict):
        raise BecaError("Unexpected Work_GetLogtimeById response.")
    return data


def existing_log_date(existing: dict[str, Any]) -> date:
    normalized = log_row_date(existing)
    if not normalized:
        raise BecaError("Existing logtime response did not include a log date.")
    return datetime.strptime(normalized, "%Y-%m-%d").date()


def first_text(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def prepare_update_logtime(client: BecaClient, args: argparse.Namespace) -> PreparedLogtimeUpdate:
    log_id = str(args.log_id).strip()
    existing = get_logtime(client, log_id)
    if existing.get("CheckinId") or existing.get("checkinId"):
        raise BecaError("This logtime row is linked to check-in; edit is not supported in v1.")

    form_id, _step_id = get_api_setting(client)
    user = get_current_user(client)
    current_email = first_text(user.get("email")).lower()
    existing_email = first_text(existing.get("Email"), existing.get("email")).lower()
    if not current_email or not existing_email:
        raise BecaError("Cannot verify logtime owner because email is missing.")
    if current_email != existing_email:
        raise BecaError("Cannot edit logtime owned by another user.")

    log_user_workflow_id = first_text(existing.get("UserWorkflowId"), existing.get("userWorkflowId"))
    if not log_user_workflow_id:
        raise BecaError("Existing logtime response did not include UserWorkflowId.")

    project_id = first_text(existing.get("Duan"), existing.get("projectId"))
    work_id = first_text(existing.get("Congviec"), existing.get("workId"))
    if not project_id or not work_id:
        raise BecaError("Existing logtime response did not include project/work id.")

    old_date = existing_log_date(existing)
    log_date = parse_log_date(args.date) if args.date else old_date
    hours = api_number_text(args.hours if args.hours is not None else existing.get("SoGio"))
    if not hours or hours == "None":
        raise BecaError("Existing logtime response did not include hours; pass --hours.")
    old_hours = api_number_text(existing.get("SoGio") or "0") if log_date == old_date else "0"
    action = args.action if args.action is not None else first_text(existing.get("Hanhdong"), "Thực hiện")
    if args.description is None:
        description = first_text(existing.get("Mota"), existing.get("description"))
    elif not args.description.strip():
        raise BecaError("--description cannot be blank when updating description.")
    else:
        description = html_description(args.description)

    over_logtime = response_data(check_over_logtime(client, log_date, hours, old_hours))
    over_logtime_number = number_or_none(over_logtime)
    if over_logtime_number is None:
        raise BecaError(f"Unexpected over-logtime validation response: {over_logtime!r}")
    if over_logtime_number < 0:
        raise BecaError(f"Over-logtime validation failed: {over_logtime}")

    duplicates = duplicate_logs(
        client,
        work_id,
        log_date,
        exclude_log_id=log_id,
        exclude_user_workflow_id=log_user_workflow_id,
    )
    if duplicates and not args.allow_duplicate:
        raise BecaError(
            f"Found {len(duplicates)} other logtime row(s) for work {work_id} on {log_date}."
        )

    existing_user_id = first_text(existing.get("UserId"), existing.get("userId"))
    values = {
        "Nguoilap": first_text(existing.get("Nguoilap"), existing.get("fullName"), user.get("fullName")),
        "Ngaylap": first_text(existing.get("Ngaylap"), existing.get("createdAt"), now_string()),
        "Email": first_text(existing.get("Email"), existing.get("email"), user.get("email")),
        "Duan": project_id,
        "Congviec": work_id,
        "Ngay": f"{log_date.isoformat()} 00:00:00",
        "SoGio": hours,
        "Hanhdong": action,
        "Mota": description,
        "UserId": normalize_user_id(existing_user_id or user.get("id")),
    }
    payload = build_form_payload(values)
    update_url = (
        f"/api/ApiEoffice/Eoffice_UpdateData?"
        f"{urlencode({'urlStr': 'Work_UpdateFormJson', 'apiId': form_id, 'workId': log_user_workflow_id})}"
    )
    return PreparedLogtimeUpdate(
        form_id=form_id,
        update_url=update_url,
        payload=payload,
        user=user,
        existing=existing,
        log_user_workflow_id=log_user_workflow_id,
        over_logtime=over_logtime,
        duplicates=duplicates,
        old_value_for_validation=old_hours,
    )


def flatten_logtime_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in items:
        children = item.get("children") or []
        if isinstance(children, list) and children:
            rows.extend(flatten_logtime_items(children))
            continue
        rows.append(item)
    return rows


def summarize_logtime_api_row(row: dict[str, Any]) -> dict[str, Any]:
    source = parse_json_object(row.get("data"))
    log_id = first_text(row.get("Id"), row.get("id"), source.get("Id"), source.get("id"), row.get("logId"), row.get("_id"))
    log_user_workflow_id = first_text(
        row.get("UserWorkflowId"),
        row.get("userWorkflowId"),
        source.get("UserWorkflowId"),
        source.get("userWorkflowId"),
    )
    hours = first_value(row.get("SoGio"), row.get("total"), source.get("SoGio"))
    return {
        "logId": log_id or None,
        "logUserWorkflowId": log_user_workflow_id or None,
        "projectId": first_text(row.get("Duan"), source.get("Duan"), row.get("projectId"), source.get("projectId")) or None,
        "workId": first_text(row.get("Congviec"), source.get("Congviec"), row.get("workId"), source.get("workId")) or None,
        "title": first_text(row.get("Title"), row.get("title"), row.get("workLogtime"), source.get("Title")) or None,
        "date": log_row_date(row) or log_row_date(source) or normalize_date_value(row.get("date") or source.get("date")),
        "hours": api_number_text(hours) if hours is not None else None,
        "description": first_text(row.get("Mota"), row.get("description"), source.get("Mota")) or None,
        "email": first_text(row.get("Email"), row.get("email"), source.get("Email"), source.get("email")) or None,
        "userId": first_text(row.get("UserId"), row.get("userId"), source.get("UserId"), source.get("userId")) or None,
    }


def summarize_logtime_row(row: dict[str, Any]) -> dict[str, Any]:
    return summarize_logtime_api_row(row)


def list_logtimes(client: BecaClient, args: argparse.Namespace) -> list[dict[str, Any]]:
    log_date = parse_log_date(args.date)
    user = get_current_user(client)
    department = first_text(args.department, user.get("departmentId"), user.get("department"))
    data = client.get_json(
        "/api/Default/Work_TimeSheetPersonalLayoutList",
        {
            "date": log_date.isoformat(),
            "projectId": args.project_id,
            "email": normalize_user_id(user.get("id")),
            "department": department,
        },
    )
    if not isinstance(data, list):
        raise BecaError("Unexpected Work_TimeSheetPersonalLayoutList response.")
    rows = [summarize_logtime_row(row) for row in flatten_logtime_items(data)]
    return [row for row in rows if row["logId"] or row["logUserWorkflowId"] or row["workId"]]


def values_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    data_json = payload.get("data_json")
    if isinstance(data_json, str):
        parsed = parse_json_object(data_json)
        if parsed:
            return parsed
    values: dict[str, Any] = {}
    for item in payload.get("data", []):
        if isinstance(item, dict) and item.get("name"):
            values[str(item["name"])] = item.get("value")
    return values


def normalize_description_for_match(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def ids_match(left: Any, right: Any) -> bool:
    left_text = first_text(left)
    right_text = first_text(right)
    return bool(left_text and right_text and left_text == right_text)


def hours_match(left: Any, right: Any) -> bool:
    return api_number_text(left) == api_number_text(right)


def row_matches_expected(
    summary: dict[str, Any],
    expected: dict[str, Any],
    log_user_workflow_id: str | None = None,
) -> bool:
    if log_user_workflow_id and not ids_match(summary.get("logUserWorkflowId"), log_user_workflow_id):
        return False

    expected_date = normalize_date_value(expected.get("Ngay") or expected.get("date"))
    if expected_date and summary.get("date") != expected_date:
        return False

    expected_work_id = first_text(expected.get("Congviec"), expected.get("workId"))
    if expected_work_id and not ids_match(summary.get("workId"), expected_work_id):
        return False

    expected_project_id = first_text(expected.get("Duan"), expected.get("projectId"))
    if expected_project_id and summary.get("projectId") and not ids_match(summary.get("projectId"), expected_project_id):
        return False

    expected_hours = first_value(expected.get("SoGio"), expected.get("hours"))
    if expected_hours is not None and not hours_match(summary.get("hours"), expected_hours):
        return False

    expected_description = first_text(expected.get("Mota"), expected.get("description"))
    if expected_description and normalize_description_for_match(summary.get("description")) != normalize_description_for_match(expected_description):
        return False

    expected_email = first_text(expected.get("Email"), expected.get("email")).lower()
    if expected_email and summary.get("email") and str(summary["email"]).lower() != expected_email:
        return False

    expected_user_id = first_text(expected.get("UserId"), expected.get("userId"))
    if expected_user_id and summary.get("userId") and normalize_user_id(summary["userId"]) != normalize_user_id(expected_user_id):
        return False

    return True


def row_sort_key(summary: dict[str, Any]) -> tuple[int, int]:
    def as_int(value: Any) -> int:
        try:
            return int(float(str(value)))
        except (TypeError, ValueError):
            return 0

    return as_int(summary.get("logUserWorkflowId")), as_int(summary.get("logId"))


def compact_response(response: Any) -> Any:
    if not isinstance(response, dict):
        return response
    return {
        key: response.get(key)
        for key in ("id", "workflowCode", "status", "workflowTitle")
        if key in response
    }


def verification_result(
    operation: str,
    expected: dict[str, Any],
    matched: dict[str, Any] | None,
    candidate_count: int,
) -> dict[str, Any]:
    expected_hours = first_value(expected.get("SoGio"), expected.get("hours"))
    result = {
        "operation": operation,
        "verified": matched is not None,
        "candidateCount": candidate_count,
        "expected": {
            "projectId": first_text(expected.get("Duan"), expected.get("projectId")) or None,
            "workId": first_text(expected.get("Congviec"), expected.get("workId")) or None,
            "date": normalize_date_value(expected.get("Ngay") or expected.get("date")),
            "hours": api_number_text(expected_hours) if expected_hours is not None else None,
            "description": first_text(expected.get("Mota"), expected.get("description")) or None,
        },
    }
    if matched:
        result.update(
            {
                "matchedLogId": matched.get("logId"),
                "logUserWorkflowId": matched.get("logUserWorkflowId"),
                "projectId": matched.get("projectId"),
                "workId": matched.get("workId"),
                "title": matched.get("title"),
                "date": matched.get("date"),
                "hours": matched.get("hours"),
                "description": matched.get("description"),
            }
        )
    else:
        result["hint"] = "Saved, but API verification did not find an exact matching logtime row. Check BecaWork manually or rerun verify-logtime."
    return result


def verify_saved_logtime(
    client: BecaClient,
    operation: str,
    expected: dict[str, Any],
    log_user_workflow_id: str | None = None,
) -> dict[str, Any]:
    work_id = first_text(expected.get("Congviec"), expected.get("workId"))
    if not work_id:
        raise BecaError("Cannot verify logtime without work id.")
    data = client.get_json("/api/Default/Work_GetLogtimeByWorkId", {"WorkFlowId": work_id})
    if not isinstance(data, list):
        raise BecaError("Unexpected Work_GetLogtimeByWorkId response during verification.")

    summaries = [summarize_logtime_api_row(item) for item in data]
    matches = [
        summary
        for summary in summaries
        if row_matches_expected(summary, expected, log_user_workflow_id=log_user_workflow_id)
    ]
    matched = sorted(matches, key=row_sort_key, reverse=True)[0] if matches else None
    return verification_result(operation, expected, matched, len(matches))


def save_result_dict(operation: str, response: Any, verification: dict[str, Any]) -> dict[str, Any]:
    return {
        "saved": True,
        "operation": operation,
        "verified": verification["verified"],
        "logtime": {
            key: verification.get(key)
            for key in (
                "matchedLogId",
                "logUserWorkflowId",
                "title",
                "projectId",
                "workId",
                "date",
                "hours",
                "description",
            )
            if verification.get(key) is not None
        },
        "postVerify": verification,
        "saveResponse": compact_response(response),
    }


def preview_dict(prepared: PreparedLogtime) -> dict[str, Any]:
    data_json = json.loads(prepared.payload["data_json"])
    return {
        "formId": prepared.form_id,
        "stepId": prepared.step_id,
        "saveUrl": f"{WORK_ORIGIN}{prepared.save_url}",
        "user": {
            "id": prepared.user.get("id"),
            "fullName": prepared.user.get("fullName"),
            "email": prepared.user.get("email"),
        },
        "overLogtime": {"remainingAfterEntry": prepared.over_logtime},
        "duplicates": [
            {
                "id": log_row_id(item) or None,
                "userWorkflowId": log_row_user_workflow_id(item) or None,
                "Ngay": item.get("Ngay") or item.get("DateLogtime"),
                "SoGio": item.get("SoGio"),
                "Congviec": item.get("Congviec"),
            }
            for item in prepared.duplicates
        ],
        "values": data_json,
        "payload": prepared.payload,
    }


def preview_update_dict(prepared: PreparedLogtimeUpdate) -> dict[str, Any]:
    data_json = json.loads(prepared.payload["data_json"])
    return {
        "formId": prepared.form_id,
        "updateUrl": f"{WORK_ORIGIN}{prepared.update_url}",
        "logId": log_row_id(prepared.existing) or None,
        "logUserWorkflowId": prepared.log_user_workflow_id,
        "user": {
            "id": prepared.user.get("id"),
            "fullName": prepared.user.get("fullName"),
            "email": prepared.user.get("email"),
        },
        "validation": {
            "oldVal": prepared.old_value_for_validation,
            "remainingAfterEntry": prepared.over_logtime,
        },
        "duplicates": [
            {
                "id": log_row_id(item) or None,
                "userWorkflowId": log_row_user_workflow_id(item) or None,
                "Ngay": item.get("Ngay") or item.get("DateLogtime"),
                "SoGio": item.get("SoGio"),
                "Congviec": item.get("Congviec"),
            }
            for item in prepared.duplicates
        ],
        "values": data_json,
        "payload": prepared.payload,
    }


def print_json(data: Any) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def plain_text_description(value: Any) -> str:
    text = str(value or "")
    text = re.sub(r"<\s*br\s*/?\s*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<\s*/\s*p\s*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    return " ".join(text.split())


def display_value(value: Any, fallback: str = "-") -> str:
    text = str(value or "").strip()
    return text if text else fallback


def status_text(value: Any) -> str:
    number = number_or_none(value)
    if number is None:
        return f"Không rõ ({value!r})"
    if number < 0:
        return f"Không hợp lệ, vượt {-number:g}h"
    if number == 0:
        return "OK, đủ 8h sau khi log"
    return f"OK, còn {number:g}h có thể log trong ngày"


def render_duplicate_summary(duplicates: list[dict[str, Any]]) -> list[str]:
    if not duplicates:
        return ["Log trùng: Không có"]
    lines = [f"Log trùng: Có {len(duplicates)} dòng cần kiểm tra"]
    for item in duplicates:
        lines.append(
            "  - "
            f"Log ID {display_value(log_row_id(item))}, "
            f"UserWorkflowId {display_value(log_row_user_workflow_id(item))}, "
            f"ngày {display_value(item.get('Ngay') or item.get('DateLogtime'))}, "
            f"{display_value(api_number_text(item.get('SoGio')))}h"
        )
    return lines


def render_preview_text(preview: dict[str, Any]) -> str:
    values = preview["values"]
    lines = [
        "Preview logtime mới",
        f"- Người lập: {display_value(values.get('Nguoilap'))} <{display_value(values.get('Email'))}>",
        f"- Ngày log: {display_value(normalize_date_value(values.get('Ngay')))}",
        f"- Project ID: {display_value(values.get('Duan'))}",
        f"- Work ID: {display_value(values.get('Congviec'))}",
        f"- Số giờ: {display_value(api_number_text(values.get('SoGio')))}",
        f"- Hành động: {display_value(values.get('Hanhdong'))}",
        f"- Mô tả: {display_value(plain_text_description(values.get('Mota')))}",
        f"- Validate giờ: {status_text(preview.get('overLogtime', {}).get('remainingAfterEntry'))}",
        *render_duplicate_summary(preview.get("duplicates", [])),
    ]
    return "\n".join(lines)


def render_preview_update_text(preview: dict[str, Any]) -> str:
    values = preview["values"]
    validation = preview.get("validation", {})
    lines = [
        "Preview chỉnh sửa logtime",
        f"- Log ID hiện tại: {display_value(preview.get('logId'))}",
        f"- Log UserWorkflowId: {display_value(preview.get('logUserWorkflowId'))}",
        f"- Người lập: {display_value(values.get('Nguoilap'))} <{display_value(values.get('Email'))}>",
        f"- Ngày log: {display_value(normalize_date_value(values.get('Ngay')))}",
        f"- Project ID: {display_value(values.get('Duan'))}",
        f"- Work ID: {display_value(values.get('Congviec'))}",
        f"- Số giờ mới: {display_value(api_number_text(values.get('SoGio')))}",
        f"- Số giờ cũ để validate: {display_value(validation.get('oldVal'))}",
        f"- Hành động: {display_value(values.get('Hanhdong'))}",
        f"- Mô tả: {display_value(plain_text_description(values.get('Mota')))}",
        f"- Validate giờ: {status_text(validation.get('remainingAfterEntry'))}",
        *render_duplicate_summary(preview.get("duplicates", [])),
    ]
    return "\n".join(lines)


def render_verification_text(verification: dict[str, Any]) -> str:
    if verification.get("verified"):
        return "\n".join(
            [
                "API verify: Thành công",
                f"- Log ID: {display_value(verification.get('matchedLogId'))}",
                f"- Log UserWorkflowId: {display_value(verification.get('logUserWorkflowId'))}",
                f"- Task: {display_value(verification.get('title'))}",
                f"- Project ID: {display_value(verification.get('projectId'))}",
                f"- Work ID: {display_value(verification.get('workId'))}",
                f"- Ngày log: {display_value(verification.get('date'))}",
                f"- Số giờ: {display_value(api_number_text(verification.get('hours')))}",
                f"- Mô tả: {display_value(plain_text_description(verification.get('description')))}",
            ]
        )
    expected = verification.get("expected", {})
    return "\n".join(
        [
            "API verify: Chưa xác nhận được",
            f"- Work ID mong đợi: {display_value(expected.get('workId'))}",
            f"- Ngày mong đợi: {display_value(expected.get('date'))}",
            f"- Số giờ mong đợi: {display_value(api_number_text(expected.get('hours')))}",
            f"- Mô tả mong đợi: {display_value(plain_text_description(expected.get('description')))}",
            f"- Gợi ý: {display_value(verification.get('hint'))}",
        ]
    )


def render_save_result_text(result: dict[str, Any]) -> str:
    operation_text = "tạo mới" if result.get("operation") == "submit" else "chỉnh sửa"
    header = f"Đã {operation_text} logtime." if result.get("saved") else "Chưa ghi logtime."
    return "\n".join([header, render_verification_text(result["postVerify"])])


def print_tasks(tasks: list[dict[str, Any]], as_json: bool) -> None:
    rows = [
        {
            "projectId": str(task.get("projectId") or "").strip(),
            "projectName": task.get("projectName"),
            "workId": task.get("userWorkflowId"),
            "title": task.get("title"),
            "status": task.get("statusName"),
            "isMyWork": task.get("isMyWork"),
            "parentTitle": task.get("parentTitle"),
        }
        for task in tasks
    ]
    if as_json:
        print_json(rows)
        return
    print(f"{'PROJECT':<12} {'WORK':<12} {'MINE':<5} {'STATUS':<16} TITLE")
    for row in rows:
        print(
            f"{row['projectId']:<12} {str(row['workId'] or ''):<12} "
            f"{str(row['isMyWork']):<5} {str(row['status'] or ''):<16} {row['title']}"
        )


def print_logtimes(rows: list[dict[str, Any]], as_json: bool) -> None:
    if as_json:
        print_json(rows)
        return
    print(f"{'LOG ID':<10} {'LOG WORKFLOW':<14} {'PROJECT':<12} {'WORK':<12} {'HOURS':<7} TITLE")
    for row in rows:
        print(
            f"{str(row['logId'] or ''):<10} {str(row['logUserWorkflowId'] or ''):<14} "
            f"{str(row['projectId'] or ''):<12} {str(row['workId'] or ''):<12} "
            f"{str(row['hours'] or ''):<7} {row['title'] or ''}"
        )


def command_list_tasks(client: BecaClient, args: argparse.Namespace) -> int:
    tasks = list_tasks(client, args)
    print_tasks(tasks, args.json)
    return 0


def command_list_logtimes(client: BecaClient, args: argparse.Namespace) -> int:
    rows = list_logtimes(client, args)
    print_logtimes(rows, args.json)
    return 0


def command_get_logtime(client: BecaClient, args: argparse.Namespace) -> int:
    print_json(get_logtime(client, str(args.log_id)))
    return 0


def command_preview(client: BecaClient, args: argparse.Namespace) -> int:
    prepared = prepare_logtime(client, args)
    preview = preview_dict(prepared)
    print_json(preview) if getattr(args, "json", False) else print(render_preview_text(preview))
    return 0


def command_preview_update(client: BecaClient, args: argparse.Namespace) -> int:
    prepared = prepare_update_logtime(client, args)
    preview = preview_update_dict(prepared)
    print_json(preview) if getattr(args, "json", False) else print(render_preview_update_text(preview))
    return 0


def command_submit(client: BecaClient, args: argparse.Namespace) -> int:
    prepared = prepare_logtime(client, args)
    preview = preview_dict(prepared)
    print_json(preview) if getattr(args, "json", False) else print(render_preview_text(preview))
    if not args.yes:
        confirmation = input("Save this BecaWork logtime? [y/N]: ").strip().lower()
        if confirmation not in {"y", "yes", "submit"}:
            raise BecaError("Submission cancelled.")
    response = client.post_json(prepared.save_url, prepared.payload)
    verification = verify_saved_logtime(
        client,
        "submit",
        values_from_payload(prepared.payload),
    )
    result = save_result_dict("submit", response, verification)
    print_json(result) if getattr(args, "json", False) else print(render_save_result_text(result))
    return 0 if verification["verified"] else 2


def command_update(client: BecaClient, args: argparse.Namespace) -> int:
    prepared = prepare_update_logtime(client, args)
    preview = preview_update_dict(prepared)
    print_json(preview) if getattr(args, "json", False) else print(render_preview_update_text(preview))
    if not args.yes:
        confirmation = input("Save this BecaWork logtime edit? [y/N]: ").strip().lower()
        if confirmation not in {"y", "yes", "update"}:
            raise BecaError("Update cancelled.")
    response = client.put_json(prepared.update_url, prepared.payload)
    verification = verify_saved_logtime(
        client,
        "update",
        values_from_payload(prepared.payload),
        log_user_workflow_id=prepared.log_user_workflow_id,
    )
    result = save_result_dict("update", response, verification)
    print_json(result) if getattr(args, "json", False) else print(render_save_result_text(result))
    return 0 if verification["verified"] else 2


def command_verify_logtime(client: BecaClient, args: argparse.Namespace) -> int:
    user = get_current_user(client)
    log_date = parse_log_date(args.date)
    expected = {
        "Duan": args.project_id,
        "Congviec": args.work_id,
        "Ngay": f"{log_date.isoformat()} 00:00:00",
        "SoGio": api_number_text(args.hours) if args.hours is not None else None,
        "Mota": html_description(args.description) if args.description else None,
        "Email": user.get("email"),
        "UserId": normalize_user_id(user.get("id")),
    }
    verification = verify_saved_logtime(
        client,
        "verify",
        {key: value for key, value in expected.items() if value is not None},
        log_user_workflow_id=args.log_user_workflow_id,
    )
    print_json(verification) if getattr(args, "json", False) else print(render_verification_text(verification))
    return 0 if verification["verified"] else 2


def add_common_logtime_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--work-id", required=True)
    parser.add_argument("--date", help="YYYY-MM-DD; defaults to previous business day")
    parser.add_argument("--hours", default="8")
    parser.add_argument("--action", default="Thực hiện")
    parser.add_argument("--description", required=True)
    parser.add_argument("--allow-duplicate", action="store_true")


def add_update_logtime_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--log-id", required=True)
    parser.add_argument("--date", help="YYYY-MM-DD; defaults to the existing logtime date")
    parser.add_argument("--hours")
    parser.add_argument("--action")
    parser.add_argument("--description")
    parser.add_argument("--allow-duplicate", action="store_true")


def add_verify_logtime_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--work-id", required=True)
    parser.add_argument("--date", help="YYYY-MM-DD; defaults to previous business day")
    parser.add_argument("--project-id")
    parser.add_argument("--log-user-workflow-id")
    parser.add_argument("--hours")
    parser.add_argument("--description")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Preview, submit, and update BecaWork logtime.")
    parser.add_argument("--cookie", default=os.getenv("BECA_COOKIE"), help="BecaWork cookie header value")
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list-tasks", help="List active tasks from BecaWork")
    list_parser.add_argument("--title")
    list_parser.add_argument("--project-name")
    list_parser.add_argument("--type", default="Xử lý")
    list_parser.add_argument("--page", type=int, default=1)
    list_parser.add_argument("--rows", type=int, default=15)
    list_parser.add_argument("--mine-only", action="store_true")
    list_parser.add_argument("--json", action="store_true")
    list_parser.set_defaults(func=command_list_tasks)

    logtimes_parser = subparsers.add_parser("list-logtimes", help="List personal logtime rows for a day")
    logtimes_parser.add_argument("--date", help="YYYY-MM-DD; defaults to previous business day")
    logtimes_parser.add_argument("--project-id", default="-1")
    logtimes_parser.add_argument("--department", help="Department id; defaults to current user's department")
    logtimes_parser.add_argument("--json", action="store_true")
    logtimes_parser.set_defaults(func=command_list_logtimes)

    get_logtime_parser = subparsers.add_parser("get-logtime", help="Read one logtime row by log id")
    get_logtime_parser.add_argument("--log-id", required=True)
    get_logtime_parser.set_defaults(func=command_get_logtime)

    preview_parser = subparsers.add_parser("preview", help="Build and validate a logtime payload")
    add_common_logtime_args(preview_parser)
    preview_parser.add_argument("--json", action="store_true", help="Print machine-readable JSON instead of review text")
    preview_parser.set_defaults(func=command_preview)

    submit_parser = subparsers.add_parser("submit", help="Submit logtime after explicit confirmation")
    add_common_logtime_args(submit_parser)
    submit_parser.add_argument("--yes", action="store_true", help="Skip prompt only when the user explicitly requested submit")
    submit_parser.add_argument("--json", action="store_true", help="Print machine-readable JSON instead of review text")
    submit_parser.set_defaults(func=command_submit)

    preview_update_parser = subparsers.add_parser("preview-update", help="Build and validate an existing logtime edit")
    add_update_logtime_args(preview_update_parser)
    preview_update_parser.add_argument("--json", action="store_true", help="Print machine-readable JSON instead of review text")
    preview_update_parser.set_defaults(func=command_preview_update)

    update_parser = subparsers.add_parser("update", help="Update an existing logtime after explicit confirmation")
    add_update_logtime_args(update_parser)
    update_parser.add_argument("--yes", action="store_true", help="Skip prompt only when the user explicitly requested update")
    update_parser.add_argument("--json", action="store_true", help="Print machine-readable JSON instead of review text")
    update_parser.set_defaults(func=command_update)

    verify_parser = subparsers.add_parser("verify-logtime", help="Verify a saved logtime row through the API")
    add_verify_logtime_args(verify_parser)
    verify_parser.add_argument("--json", action="store_true", help="Print machine-readable JSON instead of review text")
    verify_parser.set_defaults(func=command_verify_logtime)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    client = BecaClient(cookie=args.cookie)
    client.ensure_auth()
    return args.func(client, args)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BecaError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
