#!/usr/bin/env python3
from __future__ import annotations

import argparse
import difflib
import getpass
import html.parser
import json
import os
import platform
import re
import shlex
import subprocess
import sys
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from http.cookiejar import CookieJar
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin, urlparse
from urllib.request import HTTPRedirectHandler, HTTPCookieProcessor, Request, build_opener


WORK_ORIGIN = "https://work.becawork.vn"
SSO_ORIGIN = "https://sso.becawork.vn"
USER_AGENT = "Mozilla/5.0"
LOCAL_TIMEZONE = timezone(timedelta(hours=7), name="Asia/Ho_Chi_Minh")
MIN_PYTHON = (3, 11)
CLI_VERSION = "2.0.1"
CONFIG_SCHEMA_VERSION = 1
KEYRING_SERVICE = "beca-logtime"
LOGIN_URL = (
    "https://sso.becawork.vn/Account/Login?"
    "ReturnUrl=%2Fconnect%2Fauthorize%2Fcallback%3Fclient_id%3Dvwork.work"
    "%26redirect_uri%3Dhttps%253A%252F%252Fwork.becawork.vn%252Fsignin-oidc"
    "%26response_type%3Dcode%2520id_token"
    "%26scope%3DEOfficeAPI.read%2520openid%2520profile%2520email"
    "%26response_mode%3Dform_post"
)


class BecaError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: str = "BECA_ERROR",
        hint: str | None = None,
        data_changed: bool = False,
        exit_code: int = 1,
        required_action: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.hint = hint
        self.data_changed = data_changed
        self.exit_code = exit_code
        self.required_action = required_action

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": False,
            "error": {
                "code": self.code,
                "message": str(self),
                "hint": self.hint,
                "dataChanged": self.data_changed,
                "requiredAction": self.required_action,
            },
        }


class SameOriginRedirectHandler(HTTPRedirectHandler):
    """Do not forward an explicitly supplied Cookie header to another host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected and urlparse(req.full_url).netloc != urlparse(newurl).netloc:
            redirected.remove_header("Cookie")
        return redirected


@dataclass
class HtmlForm:
    action: str
    method: str
    inputs: dict[str, str]
    input_types: dict[str, str]


class FormParser(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.forms: list[HtmlForm] = []
        self._current: HtmlForm | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "form":
            self._current = HtmlForm(
                action=values.get("action") or "",
                method=(values.get("method") or "post").lower(),
                inputs={},
                input_types={},
            )
            self.forms.append(self._current)
            return
        if tag == "input" and self._current is not None:
            name = values.get("name")
            if name:
                self._current.inputs[name] = values.get("value") or ""
                self._current.input_types[name] = (values.get("type") or "text").lower()

    def handle_endtag(self, tag: str) -> None:
        if tag == "form":
            self._current = None

    @property
    def action(self) -> str:
        return self.forms[0].action if self.forms else ""

    @property
    def inputs(self) -> dict[str, str]:
        return self.forms[0].inputs if self.forms else {}


class TextParser(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        text = " ".join(data.split())
        if text:
            self.parts.append(text)


def parse_forms(document: str) -> list[HtmlForm]:
    parser = FormParser()
    parser.feed(document)
    return parser.forms


def absolute_form_action(form: HtmlForm, base_url: str) -> str:
    return urljoin(base_url, form.action or base_url)


def is_login_form(form: HtmlForm, base_url: str) -> bool:
    action = urlparse(absolute_form_action(form, base_url)).path.casefold()
    has_password = any(value == "password" for value in form.input_types.values())
    return has_password or "account/login" in action


def is_callback_form(form: HtmlForm, base_url: str) -> bool:
    target = urlparse(absolute_form_action(form, base_url))
    fields = {name.casefold() for name in form.inputs}
    has_oidc_fields = bool(fields & {"code", "id_token", "state", "session_state"})
    return target.netloc.casefold() == urlparse(WORK_ORIGIN).netloc.casefold() and target.path.rstrip("/").casefold().endswith("/signin-oidc") and has_oidc_fields


def is_auth_challenge(document: str, forms: list[HtmlForm]) -> bool:
    field_names = {name.casefold() for form in forms for name in form.inputs}
    if field_names & {"otp", "totp", "captcha", "verificationcode", "verification_code"}:
        return True
    lowered = document.casefold()
    return any(marker in lowered for marker in ("captcha", "one-time password", "verification code", "mã xác minh", "xác thực hai"))


def safe_auth_message(document: str) -> str | None:
    parser = TextParser()
    parser.feed(document)
    text = " ".join(parser.parts).casefold()
    messages = (
        ("locked", "Tài khoản BecaWork có thể đang bị khóa."),
        ("khóa", "Tài khoản BecaWork có thể đang bị khóa."),
        ("invalid username or password", "Tài khoản hoặc mật khẩu chưa đúng."),
        ("incorrect username or password", "Tài khoản hoặc mật khẩu chưa đúng."),
        ("tài khoản hoặc mật khẩu", "Tài khoản hoặc mật khẩu chưa đúng."),
        ("đăng nhập không thành công", "Đăng nhập BecaWork chưa thành công."),
    )
    for marker, message in messages:
        if marker in text:
            return message
    return None


def kwallet_lookup(entry: str) -> str | None:
    wallet = os.getenv("BECA_KWALLET", "kdewallet")
    folder = os.getenv("BECA_KWALLET_FOLDER")
    command = ["kwallet-query"]
    if folder:
        command.extend(["-f", folder])
    command.extend(["-r", f"beca-logtime:{entry}", wallet])
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    value = result.stdout.rstrip("\n")
    return value or None


def secret_tool_lookup(attributes: dict[str, str]) -> str | None:
    command = ["secret-tool", "lookup"]
    for key, value in attributes.items():
        command.extend([key, value])
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    value = result.stdout.rstrip("\n")
    return value or None


def config_dir(
    platform_name: str | None = None,
    environment: dict[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    env = environment if environment is not None else dict(os.environ)
    override = env.get("BECA_CONFIG_DIR")
    if override:
        return Path(override).expanduser()
    current_platform = platform_name or sys.platform
    user_home = home or Path.home()
    if current_platform.startswith("win"):
        return Path(env.get("APPDATA") or user_home / "AppData" / "Roaming") / "beca-logtime"
    if current_platform == "darwin":
        return user_home / "Library" / "Application Support" / "beca-logtime"
    return Path(env.get("XDG_CONFIG_HOME") or user_home / ".config") / "beca-logtime"


def config_file(**kwargs: Any) -> Path:
    return config_dir(**kwargs) / "config.json"


def load_config(path: Path | None = None) -> dict[str, Any]:
    target = path or config_file()
    if not target.exists():
        return {}
    try:
        value = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BecaError(
            "Không thể đọc cấu hình BecaWork.",
            code="CONFIG_INVALID",
            hint=f"Kiểm tra hoặc chạy reset-auth cho {target}.",
        ) from exc
    if not isinstance(value, dict) or value.get("schemaVersion") != CONFIG_SCHEMA_VERSION:
        raise BecaError(
            "Phiên bản cấu hình BecaWork không tương thích.",
            code="CONFIG_INVALID",
            hint="Chạy reset-auth rồi thiết lập lại BecaWork.",
        )
    return value


def save_config(value: dict[str, Any], path: Path | None = None) -> Path:
    target = path or config_file()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".tmp")
    payload = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    try:
        temporary.write_text(payload, encoding="utf-8")
        if os.name != "nt":
            temporary.chmod(0o600)
        os.replace(temporary, target)
    except OSError as exc:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise BecaError(
            "Không thể lưu cấu hình BecaWork.",
            code="CONFIG_WRITE_FAILED",
            hint=f"Kiểm tra quyền ghi tại {target.parent}.",
        ) from exc
    return target


def keyring_module():
    try:
        import keyring  # type: ignore[import-not-found]
    except ImportError:
        return None
    return keyring


def keyring_backend_status() -> dict[str, Any]:
    keyring = keyring_module()
    if keyring is None:
        return {
            "available": False,
            "backend": None,
            "message": "Python keyring chưa được cài đặt.",
        }
    try:
        backend = keyring.get_keyring()
        priority = getattr(backend, "priority", 0)
        viable = bool(priority and float(priority) > 0)
        return {
            "available": viable,
            "backend": f"{backend.__class__.__module__}.{backend.__class__.__name__}",
            "message": None if viable else "Không có keyring backend an toàn khả dụng.",
        }
    except Exception as exc:  # keyring has backend-specific exception types
        return {
            "available": False,
            "backend": None,
            "message": f"Keyring không khả dụng: {exc.__class__.__name__}.",
        }


def keyring_lookup(username: str) -> str | None:
    keyring = keyring_module()
    if keyring is None:
        return None
    try:
        return keyring.get_password(KEYRING_SERVICE, username)
    except Exception:
        return None


def keyring_store(username: str, password: str) -> None:
    keyring = keyring_module()
    status = keyring_backend_status()
    if keyring is None or not status["available"]:
        raise BecaError(
            "Không có credential store an toàn để lưu mật khẩu.",
            code="KEYRING_UNAVAILABLE",
            hint="Cài package keyring hoặc tiếp tục với --no-store.",
        )
    try:
        keyring.set_password(KEYRING_SERVICE, username, password)
    except Exception as exc:
        raise BecaError(
            "Không thể lưu mật khẩu vào credential store của hệ điều hành.",
            code="KEYRING_WRITE_FAILED",
            hint="Cho phép truy cập keychain/credential manager hoặc dùng --no-store.",
        ) from exc


def keyring_delete(username: str) -> bool:
    keyring = keyring_module()
    if keyring is None:
        return False
    try:
        keyring.delete_password(KEYRING_SERVICE, username)
        return True
    except Exception:
        return False


def setup_config(username: str, backend: str) -> dict[str, Any]:
    return {
        "schemaVersion": CONFIG_SCHEMA_VERSION,
        "username": username,
        "credentialBackend": backend,
        "setupCompletedAt": datetime.now(LOCAL_TIMEZONE).isoformat(),
        "lastVerifiedVersion": CLI_VERSION,
    }


def setup_command_text() -> str:
    arguments = [sys.executable, str(Path(__file__).resolve()), "setup"]
    if sys.platform.startswith("win"):
        return subprocess.list2cmdline(arguments)
    return shlex.join(arguments)


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


@dataclass
class PreparedStatusUpdate:
    work_id: str
    detail: dict[str, Any]
    target: dict[str, Any]
    validations: list[dict[str, Any]]


@dataclass
class PreparedProgressUpdate:
    work_id: str
    detail: dict[str, Any]
    progress: str
    validations: list[dict[str, Any]]


@dataclass
class DailyEntry:
    raw: str
    task_query: str
    hours: str
    description: str
    result: str | None = None
    blockers: str | None = None
    next_step: str | None = None
    progress: str | None = None


@dataclass
class PreparedDailyEntry:
    entry: DailyEntry
    task: dict[str, Any]
    logtime: PreparedLogtime
    progress: PreparedProgressUpdate | None = None


@dataclass
class PreparedDaily:
    log_date: date
    entries: list[PreparedDailyEntry]
    batch_over_logtime: Any
    total_hours: str


class BecaClient:
    def __init__(self, cookie: str | None = None) -> None:
        self.cookie = cookie
        self.opener = build_opener(
            SameOriginRedirectHandler(),
            HTTPCookieProcessor(CookieJar()),
        )
        self._xsrf_token: str | None = None
        self.auth_source: str | None = "cookie" if cookie else None
        self.authenticated_user: dict[str, Any] | None = None

    def ensure_auth(self, interactive: bool = True) -> None:
        if self.cookie:
            self.verify_login()
            return
        config = load_config()
        username = (
            os.getenv("BECA_USERNAME")
            or str(config.get("username") or "").strip()
            or secret_tool_lookup({"service": "becawork", "kind": "username"})
            or kwallet_lookup("username")
        )
        if not username:
            if not interactive or not sys.stdin.isatty():
                raise BecaError(
                    "BecaWork chưa được thiết lập trên máy này.",
                    code="SETUP_REQUIRED",
                    hint=f"Mở terminal tương tác và chạy: {setup_command_text()}. Không dùng browser hoặc Computer Use.",
                    exit_code=2,
                    required_action="OPEN_INTERACTIVE_TERMINAL",
                )
            username = input("BecaWork username: ")

        password = os.getenv("BECA_PASSWORD")
        if password:
            self.auth_source = "environment"
        if not password:
            password = keyring_lookup(username)
            if password:
                self.auth_source = "keyring"
        if not password:
            password = secret_tool_lookup(
                {"service": "becawork", "kind": "password", "username": username}
            ) or kwallet_lookup("password")
            if password:
                self.auth_source = "legacy-linux"
        if not password:
            if not interactive or not sys.stdin.isatty():
                raise BecaError(
                    "Không tìm thấy credential BecaWork cho tài khoản đã cấu hình.",
                    code="SETUP_REQUIRED",
                    hint=f"Mở terminal tương tác và chạy: {setup_command_text()}. Không dùng browser hoặc Computer Use.",
                    exit_code=2,
                    required_action="OPEN_INTERACTIVE_TERMINAL",
                )
            password = getpass.getpass("BecaWork password: ")
            self.auth_source = "interactive"
        self.login(username, password)

    def login(self, username: str, password: str) -> None:
        login_page = self._open(Request(LOGIN_URL, headers=self._headers(target_url=LOGIN_URL)))
        login_html = login_page.read().decode("utf-8", errors="replace")

        login_forms = parse_forms(login_html)
        login_form = next((form for form in login_forms if is_login_form(form, login_page.geturl())), None)
        if login_form is None:
            raise BecaError(
                "Không tìm thấy form đăng nhập BecaWork.",
                code="AUTH_FLOW_CHANGED",
                hint="SSO có thể vừa thay đổi; chạy doctor --debug và báo cho người duy trì skill.",
            )

        form_data = {
            **login_form.inputs,
            "Username": username,
            "Password": password,
            "Input.Username": username,
            "Input.Password": password,
            "RememberMe": "false",
            "button": "login",
        }
        login_action = absolute_form_action(login_form, login_page.geturl())
        response = self._open(
            Request(
                login_action,
                data=urlencode(form_data).encode("utf-8"),
                headers=self._headers(
                    {
                        "Content-Type": "application/x-www-form-urlencoded",
                        "Origin": SSO_ORIGIN,
                        "Referer": LOGIN_URL,
                    },
                    target_url=login_action,
                ),
                method="POST",
            )
        )
        body = response.read().decode("utf-8", errors="replace")
        forms = parse_forms(body)
        callback = next((form for form in forms if is_callback_form(form, response.geturl())), None)
        if callback is not None:
            self._submit_form(callback, response.geturl())
            self.authenticated_user = self.verify_login()
            return
        if is_auth_challenge(body, forms):
            raise BecaError(
                "BecaWork yêu cầu một bước xác minh bổ sung.",
                code="AUTH_CHALLENGE_REQUIRED",
                hint="Skill v2 chưa tự động hóa MFA/CAPTCHA; hãy đăng nhập trên trình duyệt hoặc liên hệ người duy trì skill.",
            )
        if any(is_login_form(form, response.geturl()) for form in forms) or "Account/Login" in response.geturl():
            raise BecaError(
                safe_auth_message(body) or "Đăng nhập BecaWork chưa thành công.",
                code="AUTH_INVALID",
                hint="Kiểm tra tài khoản hoặc mật khẩu và thử lại.",
            )
        try:
            self.authenticated_user = self.verify_login()
        except BecaError as exc:
            raise BecaError(
                "Không xác minh được phiên đăng nhập BecaWork.",
                code="AUTH_FLOW_CHANGED",
                hint="SSO có thể vừa thay đổi; chạy doctor --debug và báo cho người duy trì skill.",
            ) from exc

    def verify_login(self) -> dict[str, Any]:
        user = self.get_json("/api/Default/Work_GetInfLogin", {"IsMobile": "false"})
        if not isinstance(user, dict) or not any(user.get(key) for key in ("id", "email", "fullName")):
            raise BecaError(
                "BecaWork không trả về thông tin tài khoản hợp lệ.",
                code="AUTH_VERIFY_FAILED",
                hint="Chạy setup hoặc doctor để đăng nhập lại.",
            )
        self.authenticated_user = user
        return user

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
            Request(url, data=data, headers=self._headers(headers, target_url=url), method=method)
        )
        body = response.read().decode("utf-8", errors="replace")
        forms = parse_forms(body)
        callback = next((form for form in forms if is_callback_form(form, response.geturl())), None)
        if callback is not None:
            self._submit_form(callback, response.geturl())
            response = self._open(
                Request(url, data=data, headers=self._headers(headers, target_url=url), method=method)
            )
            body = response.read().decode("utf-8", errors="replace")
            forms = parse_forms(body)
        if is_auth_challenge(body, forms):
            raise BecaError(
                "BecaWork yêu cầu một bước xác minh bổ sung.",
                code="AUTH_CHALLENGE_REQUIRED",
                hint="Đăng nhập lại trên trình duyệt hoặc liên hệ người duy trì skill.",
            )
        if "Account/Login" in response.geturl() or any(is_login_form(form, response.geturl()) for form in forms):
            raise BecaError(
                "Phiên BecaWork chưa được xác thực hoặc đã hết hạn.",
                code="AUTH_REQUIRED",
                hint="Chạy setup để đăng nhập lại.",
            )
        return body

    def _submit_html_form(self, html: str, base_url: str) -> str:
        forms = parse_forms(html)
        form = next((item for item in forms if is_callback_form(item, base_url)), None)
        if form is None:
            raise BecaError(
                "Không tìm thấy callback form hợp lệ của BecaWork.",
                code="AUTH_FLOW_CHANGED",
                hint="SSO có thể vừa thay đổi; chạy doctor --debug và báo cho người duy trì skill.",
            )
        return self._submit_form(form, base_url)

    def _submit_form(self, form: HtmlForm, base_url: str) -> str:
        action = absolute_form_action(form, base_url)
        response = self._open(
            Request(
                action,
                data=urlencode(form.inputs).encode("utf-8"),
                headers=self._headers(
                    {
                        "Content-Type": "application/x-www-form-urlencoded",
                        "Origin": origin_for(action),
                        "Referer": base_url,
                    },
                    target_url=action,
                ),
                method="POST",
            )
        )
        return response.read().decode("utf-8", errors="replace")

    def _open(self, request: Request):
        try:
            return self.opener.open(request, timeout=30)
        except HTTPError as exc:
            raise BecaError(
                f"BecaWork trả về HTTP {exc.code}.",
                code="HTTP_ERROR",
                hint=f"Kiểm tra kết nối hoặc chạy doctor. Endpoint: {urlparse(request.full_url).path}",
            ) from exc
        except URLError as exc:
            raise BecaError(
                "Không thể kết nối tới BecaWork.",
                code="NETWORK_ERROR",
                hint="Kiểm tra Internet, VPN, proxy hoặc DNS rồi chạy doctor.",
            ) from exc

    def _headers(
        self,
        headers: dict[str, str] | None = None,
        target_url: str | None = None,
    ) -> dict[str, str]:
        merged = {
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
            **(headers or {}),
        }
        target_host = urlparse(target_url or WORK_ORIGIN).netloc.casefold()
        work_host = urlparse(WORK_ORIGIN).netloc.casefold()
        if self.cookie and target_host == work_host:
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
            raise BecaError(
                f"BecaWork trả về dữ liệu không hợp lệ cho {urlparse(url).path}.",
                code="INVALID_RESPONSE",
                hint="Phiên đăng nhập có thể đã hết hạn hoặc API vừa thay đổi; chạy doctor.",
            ) from exc


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


def structured_description(
    done: str,
    result: str | None = None,
    blockers: str | None = None,
    next_step: str | None = None,
) -> str:
    """Build the four-section HTML currently supplied by the BecaWork form."""
    stripped = str(done or "").strip()
    extra_values = (result, blockers, next_step)
    if stripped.startswith("<") and not any(value is not None for value in extra_values):
        return done
    if not stripped:
        raise BecaError("Description/Đã thực hiện is required.")
    sections = (
        ("Đã thực hiện", stripped),
        ("Kết quả", str(result or "").strip()),
        ("Vướng mắc", str(blockers or "").strip()),
        ("Bước tiếp theo", str(next_step or "").strip()),
    )
    items = "".join(
        f"<li><p><em>{html.escape(label)}</em>: {html.escape(value)}</p></li>"
        for label, value in sections
    )
    return f"<ul>{items}</ul>"


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


def validate_hours(value: Any, form_format: list[dict[str, Any]] | None = None) -> str:
    number = number_or_none(value)
    if number is None or not number.is_integer():
        raise BecaError("Logtime hours must be a whole number.")

    minimum = 1.0
    maximum = 16.0
    if form_format is not None:
        field = find_field(form_format, "SoGio")
        if not field:
            raise BecaError("BecaWork logtime form no longer contains the SoGio field.")
        if field.get("type") != "number":
            raise BecaError(f"BecaWork SoGio contract changed: expected type=number, got {field.get('type')!r}.")
        decimal_places = number_or_none(field.get("numberOfDecimalPlaces"))
        if decimal_places != 0:
            raise BecaError(
                "BecaWork SoGio contract changed: expected numberOfDecimalPlaces=0. Run a contract audit before submitting."
            )
        field_minimum = number_or_none(field.get("minimum"))
        field_maximum = number_or_none(field.get("maximum"))
        if field_minimum is not None:
            minimum = field_minimum
        if field_maximum is not None:
            maximum = field_maximum

    if number < minimum or number > maximum:
        raise BecaError(f"Logtime hours must be between {api_number_text(minimum)} and {api_number_text(maximum)}.")
    return api_number_text(number)


def validate_action(value: Any, form_format: list[dict[str, Any]]) -> str:
    action = str(value or "").strip()
    field = find_field(form_format, "Hanhdong")
    if not field:
        raise BecaError("BecaWork logtime form no longer contains the Hanhdong field.")
    select_items = field.get("selectItems")
    if not isinstance(select_items, list) or not select_items:
        raise BecaError("BecaWork Hanhdong contract changed: selectItems is missing or empty.")
    allowed = [str(item.get("value") or "").strip() for item in select_items if isinstance(item, dict)]
    allowed = [item for item in allowed if item]
    if action not in allowed:
        raise BecaError(f"Unsupported logtime action {action!r}. Allowed values: {', '.join(allowed)}.")
    return action


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
    if isinstance(value, dict) and "data" in value:
        keys = set(value)
        envelope_keys = {
            "data", "status", "message", "hasErrors", "success", "isSuccess",
            "errors", "traceId", "timestamp", "requestId",
        }
        strong_markers = {"message", "hasErrors", "success", "isSuccess", "errors", "traceId"}
        if keys <= envelope_keys or keys & strong_markers:
            return value.get("data")
    return value


def api_truthy(value: Any) -> bool:
    if value is True or value == 1:
        return True
    return str(value or "").strip().casefold() in {"true", "1", "yes"}


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
    data = response_data(client.get_json("/api/Default/Work_GetWorkInProcess", params))
    if not isinstance(data, list):
        raise BecaError("Unexpected Work_GetWorkInProcess response.")
    tasks = flatten_tasks(data)
    if args.mine_only:
        tasks = [task for task in tasks if api_truthy(task.get("isMyWork"))]
    return tasks


def comment_date(comment: dict[str, Any]) -> str | None:
    return normalize_date_value(comment.get("lastModified") or comment.get("LastModified"))


def list_comments(client: BecaClient, args: argparse.Namespace) -> list[dict[str, Any]]:
    since_date = parse_log_date(args.since)
    if args.work_id:
        detail = get_task_detail(client, str(args.work_id))
        tasks = [detail]
    else:
        task_args = argparse.Namespace(
            title="",
            project_name="",
            type=args.type,
            page=1,
            rows=args.rows,
            mine_only=True,
        )
        tasks = list_tasks(client, task_args)

    rows: list[dict[str, Any]] = []
    for task in tasks:
        work_id = task_work_id(task)
        if not work_id:
            continue
        data = response_data(
            client.get_json("/api/Default/Work_GetComment", {"workId": work_id})
        )
        if data is None:
            data = []
        if not isinstance(data, list):
            raise BecaError(f"Unexpected Work_GetComment response for work {work_id}.")
        for comment in data:
            if not isinstance(comment, dict):
                continue
            modified_date = comment_date(comment)
            if not modified_date or modified_date < since_date.isoformat():
                continue
            comment_text = plain_text_description(comment.get("note") or comment.get("Note"))
            if not getattr(args, "show_sensitive", False):
                comment_text = redact_sensitive_text(comment_text)
            rows.append(
                {
                    "commentId": first_value(comment.get("id"), comment.get("Id")),
                    "workId": first_text(comment.get("workId"), comment.get("WorkId"), work_id),
                    "task": first_text(
                        comment.get("workName"),
                        comment.get("WorkName"),
                        task_title(task),
                    ),
                    "author": first_text(comment.get("fullName"), comment.get("FullName")),
                    "comment": comment_text,
                    "lastModified": first_text(
                        comment.get("lastModified"), comment.get("LastModified")
                    ),
                }
            )
    return sorted(
        rows,
        key=lambda row: (normalize_date_value(row.get("lastModified")) or "", str(row.get("commentId") or "")),
        reverse=True,
    )


def parse_daily_entry(raw: str) -> DailyEntry:
    parts = [part.strip() for part in str(raw or "").split("|")]
    if len(parts) < 3:
        raise BecaError(
            'Daily entry must use: "task | hours | description [| result=... | blockers=... | next=... | progress=28]".'
        )
    task_query, hours_raw, description = parts[:3]
    if not task_query:
        raise BecaError("Daily entry task query is required.")
    hours = validate_hours(hours_raw)
    if not description:
        raise BecaError("Daily entry description is required.")
    options: dict[str, str] = {}
    aliases = {"next-step": "next", "next_step": "next", "blocker": "blockers"}
    for option in parts[3:]:
        if "=" not in option:
            raise BecaError(f"Daily entry option must use key=value: {option!r}.")
        key, value = option.split("=", 1)
        key = aliases.get(key.strip().lower(), key.strip().lower())
        if key not in {"result", "blockers", "next", "progress"}:
            raise BecaError(f"Unsupported daily entry option: {key}.")
        if key in options:
            raise BecaError(f"Duplicate daily entry option: {key}.")
        options[key] = value.strip()
    progress = normalize_progress_percent(options["progress"]) if "progress" in options else None
    return DailyEntry(
        raw=raw,
        task_query=task_query,
        hours=hours,
        description=description,
        result=options.get("result"),
        blockers=options.get("blockers"),
        next_step=options.get("next"),
        progress=progress,
    )


def normalize_task_query(value: Any) -> str:
    text = unicodedata.normalize("NFD", str(value or ""))
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    text = text.replace("đ", "d").replace("Đ", "D").casefold()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def task_work_id(task: dict[str, Any]) -> str:
    return first_text(task.get("userWorkflowId"), task.get("UserWorkflowId"), task.get("workId"))


def task_project_id(task: dict[str, Any]) -> str:
    return first_text(task.get("projectId"), task.get("ProjectId")).strip()


def task_title(task: dict[str, Any]) -> str:
    return first_text(task.get("title"), task.get("Title"))


def daily_match_score(query: str, task: dict[str, Any]) -> float:
    normalized_query = normalize_task_query(query)
    normalized_title = normalize_task_query(task_title(task))
    if not normalized_query or not normalized_title:
        return 0.0
    if normalized_query == normalized_title:
        return 1.0
    if normalized_query in normalized_title:
        return 0.9 + min(len(normalized_query) / max(len(normalized_title), 1), 0.09)
    if normalized_title in normalized_query:
        return 0.88
    return difflib.SequenceMatcher(None, normalized_query, normalized_title).ratio()


def task_candidate_summary(task: dict[str, Any], score: float | None = None) -> dict[str, Any]:
    summary = {
        "projectId": task_project_id(task) or None,
        "projectName": first_text(task.get("projectName"), task.get("ProjectName")) or None,
        "workId": task_work_id(task) or None,
        "title": task_title(task) or None,
        "status": first_text(task.get("statusName"), task.get("StatusName")) or None,
    }
    if score is not None:
        summary["score"] = round(score, 3)
    return summary


def daily_match_candidates(query: str, tasks: list[dict[str, Any]]) -> list[tuple[float, dict[str, Any]]]:
    query_text = str(query or "").strip()
    if query_text:
        exact_id_matches = [(1.0, task) for task in tasks if task_work_id(task) == query_text]
        if exact_id_matches:
            return exact_id_matches
    scored = [(daily_match_score(query, task), task) for task in tasks]
    return sorted(scored, key=lambda item: item[0], reverse=True)


def resolve_daily_task(query: str, tasks: list[dict[str, Any]]) -> dict[str, Any]:
    candidates = daily_match_candidates(query, tasks)
    viable = [(score, task) for score, task in candidates if score >= 0.62]
    if not viable:
        suggestions = [task_candidate_summary(task, score) for score, task in candidates[:5]]
        raise BecaError(f"Could not match daily task {query!r}. Candidates: {json.dumps(suggestions, ensure_ascii=False)}")

    best_score, best_task = viable[0]
    if len(viable) > 1:
        second_score = viable[1][0]
        if (best_score - second_score) < 0.12:
            suggestions = [task_candidate_summary(task, score) for score, task in viable[:5]]
            raise BecaError(f"Ambiguous daily task {query!r}. Candidates: {json.dumps(suggestions, ensure_ascii=False)}")
    return best_task


def daily_task_list_args() -> argparse.Namespace:
    return argparse.Namespace(
        title="",
        project_name="",
        type="Xử lý",
        page=1,
        rows=200,
        mine_only=True,
    )


def daily_logtime_args(args: argparse.Namespace, entry: DailyEntry, task: dict[str, Any]) -> argparse.Namespace:
    return argparse.Namespace(
        project_id=task_project_id(task),
        work_id=task_work_id(task),
        date=args.date,
        hours=entry.hours,
        action="Thực hiện",
        description=entry.description,
        result=entry.result,
        blockers=entry.blockers,
        next_step=entry.next_step,
        allow_duplicate=args.allow_duplicate,
    )


def daily_progress_args(entry: DailyEntry, task: dict[str, Any]) -> argparse.Namespace:
    return argparse.Namespace(
        work_id=task_work_id(task),
        progress=entry.progress,
    )


def prepare_daily(client: BecaClient, args: argparse.Namespace) -> PreparedDaily:
    if not args.entry:
        raise BecaError("Pass at least one --entry.")
    entries = [parse_daily_entry(raw) for raw in args.entry]
    total_number = sum(number_or_none(entry.hours) or 0 for entry in entries)
    if abs(total_number - 8.0) > 0.000001:
        raise BecaError(f"Daily total hours must be exactly 8; got {total_number:g}.")

    log_date = parse_log_date(args.date)
    batch_over_logtime = response_data(check_over_logtime(client, log_date, "8", "0"))
    batch_over_number = number_or_none(batch_over_logtime)
    if batch_over_number is None:
        raise BecaError(f"Unexpected daily over-logtime validation response: {batch_over_logtime!r}")
    if batch_over_number < 0:
        raise BecaError(f"Daily over-logtime validation failed: {batch_over_logtime}")

    tasks = list_tasks(client, daily_task_list_args())
    if not tasks:
        raise BecaError("No active personal tasks found.")

    prepared_entries: list[PreparedDailyEntry] = []
    seen_work_ids: set[str] = set()
    for entry in entries:
        task = resolve_daily_task(entry.task_query, tasks)
        work_id = task_work_id(task)
        if not work_id:
            raise BecaError(f"Matched task for {entry.task_query!r} did not include work id.")
        if not task_project_id(task):
            raise BecaError(f"Matched task for {entry.task_query!r} did not include project id.")
        if work_id in seen_work_ids and not args.allow_duplicate:
            raise BecaError(f"Daily contains duplicate entry for work {work_id}.")
        seen_work_ids.add(work_id)

        logtime = prepare_logtime(client, daily_logtime_args(args, entry, task))
        progress = prepare_progress_update(client, daily_progress_args(entry, task)) if entry.progress else None
        prepared_entries.append(
            PreparedDailyEntry(
                entry=entry,
                task=task,
                logtime=logtime,
                progress=progress,
            )
        )

    return PreparedDaily(
        log_date=log_date,
        entries=prepared_entries,
        batch_over_logtime=batch_over_logtime,
        total_hours=api_number_text(total_number),
    )


def get_task_detail(client: BecaClient, work_id: str) -> dict[str, Any]:
    data = response_data(client.get_json("/api/Default/Work_DetailInfo", {"workId": work_id}))
    if not isinstance(data, dict):
        raise BecaError("Unexpected Work_DetailInfo response.")
    if not first_text(data.get("userWorkflowId"), data.get("UserWorkflowId")):
        data["userWorkflowId"] = str(work_id)
    return data


def get_next_statuses(client: BecaClient, work_id: str, project_id: str) -> list[dict[str, Any]]:
    data = response_data(
        client.get_json(
            "/api/Default/Work_GetNextStatus",
            {"WorkId": work_id, "projectId": project_id, "useForChild": "false"},
        )
    )
    if not isinstance(data, list):
        raise BecaError("Unexpected Work_GetNextStatus response.")
    return [item for item in data if isinstance(item, dict)]


def status_workflow_id(status: dict[str, Any]) -> str:
    return first_text(status.get("userWorkflowId"), status.get("UserWorkflowId"))


def status_name(status: dict[str, Any]) -> str:
    return first_text(status.get("name"), status.get("Name"))


def status_kind(status: dict[str, Any]) -> str:
    return first_text(status.get("trangThaiCongViec"), status.get("statusType"))


def normalized_lookup_text(value: Any) -> str:
    return " ".join(str(value or "").strip().casefold().split())


def resolve_status_target(
    statuses: list[dict[str, Any]],
    status: str | None,
    status_id: str | None,
) -> dict[str, Any]:
    if bool(status) == bool(status_id):
        raise BecaError("Pass exactly one of --status or --status-id.")
    if status_id:
        target_id = str(status_id).strip()
        for item in statuses:
            if target_id == status_workflow_id(item):
                return item
        raise BecaError(
            f"Status userWorkflowId {target_id} is not available for this work. "
            "Do not pass the internal id or theSameId."
        )

    target_name = normalized_lookup_text(status)
    matches = [item for item in statuses if normalized_lookup_text(status_name(item)) == target_name]
    if not matches:
        raise BecaError(f"Status {status!r} is not available for this work.")
    if len(matches) > 1:
        ids = ", ".join(status_workflow_id(item) or first_text(item.get("id")) for item in matches)
        raise BecaError(f"Status {status!r} matched multiple options; pass --status-id. Candidates: {ids}")
    return matches[0]


def is_cancel_status(status: dict[str, Any]) -> bool:
    name = normalized_lookup_text(status_name(status))
    kind = normalized_lookup_text(status_kind(status))
    return name in {"pending", "reject"} or kind in {"hủy", "huy"}


def guard_message(value: Any) -> str:
    data = response_data(value)
    if data is None:
        return ""
    if isinstance(data, str):
        text = data.strip()
        return "" if text.casefold() in {"", "none", "null"} else text
    if isinstance(data, (list, dict)) and not data:
        return ""
    return str(data)


def run_task_update_guard(client: BecaClient, work_id: str) -> dict[str, Any]:
    value = client.get_json("/api/Default/Work_CheckRuleUpdateProcessWork", {"workId": work_id})
    message = guard_message(value)
    result = {"name": "Work_CheckRuleUpdateProcessWork", "message": message}
    if message:
        raise BecaError(f"Task update rule failed: {message}")
    return result


def run_status_guards(client: BecaClient, work_id: str, status_id: str) -> list[dict[str, Any]]:
    validations = [run_task_update_guard(client, work_id)]
    form_extend = client.get_json(
        "/api/Default/Work_CheckBeforeSaveChangStatusWithFormExtendInfo",
        {"workId": work_id},
    )
    form_extend_message = guard_message(form_extend)
    validations.append(
        {
            "name": "Work_CheckBeforeSaveChangStatusWithFormExtendInfo",
            "message": form_extend_message,
        }
    )
    if form_extend_message:
        raise BecaError(f"Status form-extend validation failed: {form_extend_message}")

    status_check = client.get_json(
        "/api/Default/Work_CheckBeforeSaveChangStatus",
        {"workId": work_id, "statusId": status_id},
    )
    status_message = guard_message(status_check)
    validations.append(
        {
            "name": "Work_CheckBeforeSaveChangStatus",
            "statusId": status_id,
            "message": status_message,
        }
    )
    if status_message:
        raise BecaError(f"Status validation failed: {status_message}")
    return validations


def prepare_status_update(client: BecaClient, args: argparse.Namespace) -> PreparedStatusUpdate:
    work_id = str(args.work_id).strip()
    detail = get_task_detail(client, work_id)
    project_id = first_text(detail.get("projectId"), detail.get("ProjectId"))
    if not project_id:
        raise BecaError("Task detail response did not include projectId.")
    statuses = get_next_statuses(client, work_id, project_id)
    target = resolve_status_target(statuses, getattr(args, "status", None), getattr(args, "status_id", None))
    target_status_id = status_workflow_id(target)
    if not target_status_id:
        raise BecaError("Target status did not include userWorkflowId.")
    if is_cancel_status(target) and not getattr(args, "allow_cancel", False):
        raise BecaError("Pending/Reject/cancel statuses are blocked by default. Pass --allow-cancel to override.")
    validations = run_status_guards(client, work_id, target_status_id)
    return PreparedStatusUpdate(work_id=work_id, detail=detail, target=target, validations=validations)


def normalize_progress_percent(value: Any) -> str:
    text = str(value or "").strip().replace(",", ".")
    if text.endswith("%"):
        text = text[:-1].strip()
    number = number_or_none(text)
    if number is None:
        raise BecaError("--progress must be a number from 0 to 100.")
    if number < 0 or number > 100:
        raise BecaError("--progress must be between 0 and 100.")
    normalized = str(int(number)) if number.is_integer() else f"{number:g}"
    return f"{normalized}%"


def progress_number(value: Any) -> float | None:
    text = str(value or "").strip().replace(",", ".")
    if text.endswith("%"):
        text = text[:-1].strip()
    return number_or_none(text)


def progress_matches(current: Any, expected: Any) -> bool:
    left = progress_number(current)
    right = progress_number(expected)
    return left is not None and right is not None and abs(left - right) < 0.000001


def prepare_progress_update(client: BecaClient, args: argparse.Namespace) -> PreparedProgressUpdate:
    work_id = str(args.work_id).strip()
    detail = get_task_detail(client, work_id)
    progress = normalize_progress_percent(args.progress)
    validations = [run_task_update_guard(client, work_id)]
    return PreparedProgressUpdate(work_id=work_id, detail=detail, progress=progress, validations=validations)


def get_api_setting(client: BecaClient) -> tuple[str, str]:
    data = response_data(client.get_json("/api/Default/Work_GetApiSetting"))
    if not isinstance(data, dict):
        raise BecaError("Unexpected Work_GetApiSetting response.")
    form_setting = data.get("id_getWorkFormLogTime")
    step_setting = data.get("id_getWorkFormLogTimeStep")
    form_id = form_setting.get("id") if isinstance(form_setting, dict) else None
    step_id = step_setting.get("id") if isinstance(step_setting, dict) else None
    if form_id in (None, "") or step_id in (None, ""):
        raise BecaError(
            "BecaWork did not return logtime formId/stepId. Refusing to use stale hard-coded workflow ids."
        )
    return str(form_id), str(step_id)


def get_form_format(client: BecaClient, form_id: str) -> list[dict[str, Any]]:
    data = response_data(client.post_json(
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
    ))
    if not isinstance(data, list):
        raise BecaError("Unexpected getFormatWorkflow response.")
    return data


def inspect_logtime_contract(client: BecaClient) -> dict[str, Any]:
    form_id, step_id = get_api_setting(client)
    form_format = get_form_format(client, form_id)
    required_fields = (
        "Nguoilap", "Ngaylap", "Email", "Duan", "Congviec",
        "Ngay", "SoGio", "Hanhdong", "Mota", "UserId",
    )
    missing = [name for name in required_fields if not find_field(form_format, name)]
    issues = [f"Missing form field: {name}" for name in missing]

    hours_field = find_field(form_format, "SoGio") or {}
    hours_contract = {
        "type": hours_field.get("type"),
        "minimum": hours_field.get("minimum"),
        "maximum": hours_field.get("maximum"),
        "numberOfDecimalPlaces": hours_field.get("numberOfDecimalPlaces"),
    }
    if hours_field:
        try:
            validate_hours("1", form_format)
            validate_hours("16", form_format)
        except BecaError as exc:
            issues.append(str(exc))

    action_field = find_field(form_format, "Hanhdong") or {}
    action_values = [
        str(item.get("value") or "").strip()
        for item in (action_field.get("selectItems") or [])
        if isinstance(item, dict) and str(item.get("value") or "").strip()
    ]
    if not action_values:
        issues.append("Hanhdong.selectItems is missing or empty.")

    description_template = default_field_value(form_format, "Mota") or ""
    template_text = plain_text_description(description_template)
    expected_sections = ("Đã thực hiện", "Kết quả", "Vướng mắc", "Bước tiếp theo")
    missing_sections = [label for label in expected_sections if label not in template_text]
    if missing_sections:
        issues.append(f"Mota.defaultValue is missing sections: {', '.join(missing_sections)}")

    return {
        "compatible": not issues,
        "formId": form_id,
        "stepId": step_id,
        "missingFields": missing,
        "hours": hours_contract,
        "actions": action_values,
        "descriptionTemplate": description_template,
        "issues": issues,
    }


def duplicate_logs(
    client: BecaClient,
    work_id: str,
    log_date: date,
    exclude_log_id: str | None = None,
    exclude_user_workflow_id: str | None = None,
) -> list[dict[str, Any]]:
    data = response_data(client.get_json("/api/Default/Work_GetLogtimeByWorkId", {"WorkFlowId": work_id}))
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
    if not args.description or not args.description.strip():
        raise BecaError("--description is required.")

    form_id, step_id = get_api_setting(client)
    form_format = get_form_format(client, form_id)
    hours = validate_hours(args.hours, form_format)
    action = validate_action(args.action, form_format)
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
        "Hanhdong": action,
        "Mota": structured_description(
            args.description,
            getattr(args, "result", None),
            getattr(args, "blockers", None),
            getattr(args, "next_step", None),
        ),
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
    form_format = get_form_format(client, form_id)
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
    raw_hours = args.hours if args.hours is not None else existing.get("SoGio")
    if raw_hours in (None, ""):
        raise BecaError("Existing logtime response did not include hours; pass --hours.")
    hours = validate_hours(raw_hours, form_format)
    old_hours = api_number_text(existing.get("SoGio") or "0") if log_date == old_date else "0"
    action = validate_action(
        args.action if args.action is not None else first_text(existing.get("Hanhdong"), "Thực hiện"),
        form_format,
    )
    if args.description is None:
        if any(getattr(args, name, None) is not None for name in ("result", "blockers", "next_step")):
            raise BecaError("Pass --description together with --result/--blockers/--next-step.")
        description = first_text(existing.get("Mota"), existing.get("description"))
    elif not args.description.strip():
        raise BecaError("--description cannot be blank when updating description.")
    else:
        description = structured_description(
            args.description,
            getattr(args, "result", None),
            getattr(args, "blockers", None),
            getattr(args, "next_step", None),
        )

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
    data = response_data(client.get_json(
        "/api/Default/Work_TimeSheetPersonalLayoutList",
        {
            "date": log_date.isoformat(),
            "projectId": args.project_id,
            "email": normalize_user_id(user.get("id")),
            "department": department,
        },
    ))
    if not isinstance(data, list):
        raise BecaError("Unexpected Work_TimeSheetPersonalLayoutList response.")
    rows = [summarize_logtime_row(row) for row in flatten_logtime_items(data)]
    target_date = log_date.isoformat()
    return [
        row
        for row in rows
        if row.get("date") == target_date
        and (row["logId"] or row["logUserWorkflowId"] or row["workId"])
    ]


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
    data = response_data(client.get_json("/api/Default/Work_GetLogtimeByWorkId", {"WorkFlowId": work_id}))
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


def redact_sensitive_text(value: str) -> str:
    text = str(value or "")
    text = re.sub(
        r"(?i)\b(password|mật\s*khẩu|passwd|pwd|api[ _-]?key|token|secret)\b(\s*[:=]\s*)([^\s,;]+)",
        lambda match: f"{match.group(1)}{match.group(2)}[REDACTED]",
        text,
    )
    text = re.sub(
        r"(?i)(tài\s*khoản(?:\s+test)?\s*:\s*)([^/\s]+)(\s*/\s*)([^\s,;]+)",
        lambda match: f"{match.group(1)}[REDACTED]{match.group(3)}[REDACTED]",
        text,
    )
    return text


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


def daily_entry_preview_dict(prepared: PreparedDailyEntry) -> dict[str, Any]:
    logtime_preview = preview_dict(prepared.logtime)
    values = logtime_preview["values"]
    return {
        "taskQuery": prepared.entry.task_query,
        "resolvedTask": task_candidate_summary(prepared.task),
        "date": normalize_date_value(values.get("Ngay")),
        "hours": api_number_text(values.get("SoGio")),
        "description": plain_text_description(values.get("Mota")),
        "action": values.get("Hanhdong"),
        "overLogtime": logtime_preview.get("overLogtime"),
        "duplicates": logtime_preview.get("duplicates", []),
        "progress": progress_preview_dict(prepared.progress) if prepared.progress else None,
        "logtime": logtime_preview,
    }


def daily_preview_dict(prepared: PreparedDaily) -> dict[str, Any]:
    return {
        "date": prepared.log_date.isoformat(),
        "totalHours": prepared.total_hours,
        "batchOverLogtime": {"remainingAfterBatch": prepared.batch_over_logtime},
        "entries": [daily_entry_preview_dict(item) for item in prepared.entries],
    }


def render_daily_preview_text(preview: dict[str, Any]) -> str:
    lines = [
        "Preview daily logtime",
        f"- Ngày log: {display_value(preview.get('date'))}",
        f"- Tổng giờ: {display_value(api_number_text(preview.get('totalHours')))}",
        f"- Validate ngày: {status_text(preview.get('batchOverLogtime', {}).get('remainingAfterBatch'))}",
        f"- Số dòng: {len(preview.get('entries', []))}",
    ]
    for index, entry in enumerate(preview.get("entries", []), start=1):
        task = entry.get("resolvedTask", {})
        lines.extend(
            [
                "",
                f"{index}. {display_value(task.get('title'))}",
                f"   Work ID: {display_value(task.get('workId'))} | Project ID: {display_value(task.get('projectId'))}",
                f"   Project: {display_value(task.get('projectName'))}",
                f"   Giờ: {display_value(api_number_text(entry.get('hours')))}",
                f"   Nội dung: {display_value(entry.get('description'))}",
                f"   Validate dòng: {status_text(entry.get('overLogtime', {}).get('remainingAfterEntry'))}",
            ]
        )
        progress = entry.get("progress")
        if progress:
            lines.append(f"   Progress sau log: {display_value(progress.get('target', {}).get('progress'))}")
        lines.extend(f"   {line}" for line in render_duplicate_summary(entry.get("duplicates", [])))
    return "\n".join(lines)


def daily_result_entry_dict(
    prepared: PreparedDailyEntry,
    logtime_result: dict[str, Any] | None = None,
    progress_result: dict[str, Any] | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    return {
        "taskQuery": prepared.entry.task_query,
        "resolvedTask": task_candidate_summary(prepared.task),
        "hours": prepared.entry.hours,
        "description": prepared.entry.description,
        "logtime": logtime_result,
        "progress": progress_result,
        "error": error,
    }


def daily_result_dict(
    prepared: PreparedDaily,
    entries: list[dict[str, Any]],
    failed_entry: dict[str, Any] | None = None,
) -> dict[str, Any]:
    complete = failed_entry is None and len(entries) == len(prepared.entries)
    verified = complete and all(
        bool((entry.get("logtime") or {}).get("verified"))
        and (entry.get("progress") is None or bool(entry["progress"].get("verified")))
        for entry in entries
    )
    return {
        "saved": any(bool((entry.get("logtime") or {}).get("saved")) for entry in entries),
        "complete": complete,
        "verified": verified,
        "date": prepared.log_date.isoformat(),
        "totalHours": prepared.total_hours,
        "entries": entries,
        "failedEntry": failed_entry,
    }


def render_daily_result_text(result: dict[str, Any]) -> str:
    header = "Đã ghi daily logtime." if result.get("complete") else "Daily logtime chưa hoàn tất."
    lines = [
        header,
        f"- Ngày log: {display_value(result.get('date'))}",
        f"- Tổng giờ: {display_value(api_number_text(result.get('totalHours')))}",
        f"- API verify toàn batch: {'Thành công' if result.get('verified') else 'Chưa xác nhận đủ'}",
    ]
    for index, entry in enumerate(result.get("entries", []), start=1):
        task = entry.get("resolvedTask", {})
        logtime = entry.get("logtime") or {}
        logtime_row = logtime.get("logtime") or {}
        progress = entry.get("progress")
        lines.extend(
            [
                "",
                f"{index}. {display_value(task.get('title'))}",
                f"   Work ID: {display_value(task.get('workId'))} | Project ID: {display_value(task.get('projectId'))}",
                f"   Giờ: {display_value(api_number_text(entry.get('hours')))}",
                f"   Nội dung: {display_value(entry.get('description'))}",
                f"   Logtime verify: {'Thành công' if logtime.get('verified') else 'Chưa xác nhận được'}",
                f"   Log ID: {display_value(logtime_row.get('matchedLogId'))} | Log UserWorkflowId: {display_value(logtime_row.get('logUserWorkflowId'))}",
            ]
        )
        if progress:
            lines.append(f"   Progress verify: {'Thành công' if progress.get('verified') else 'Chưa xác nhận được'}")
    failed = result.get("failedEntry")
    if failed:
        task = failed.get("resolvedTask", {})
        lines.extend(
            [
                "",
                "Dừng ở dòng lỗi:",
                f"- Task: {display_value(task.get('title'))}",
                f"- Work ID: {display_value(task.get('workId'))}",
                f"- Lỗi: {display_value(failed.get('error'))}",
            ]
        )
    return "\n".join(lines)


def task_summary(detail: dict[str, Any]) -> dict[str, Any]:
    executors = detail.get("userExecStatus", detail.get("UserExecStatus"))
    if isinstance(executors, list):
        executor_summary = [
            {
                "id": first_text(item.get("id"), item.get("userId")) or None,
                "fullName": first_text(item.get("fullName"), item.get("name")) or None,
                "email": first_text(item.get("email")) or None,
            }
            for item in executors
            if isinstance(item, dict)
        ]
    else:
        executor_summary = executors
    return {
        "workId": first_text(detail.get("userWorkflowId"), detail.get("UserWorkflowId")) or None,
        "title": first_text(detail.get("title"), detail.get("Title")) or None,
        "projectId": first_text(detail.get("projectId"), detail.get("ProjectId")) or None,
        "projectName": first_text(detail.get("projectName"), detail.get("ProjectName")) or None,
        "statusId": first_text(detail.get("status"), detail.get("Status")) or None,
        "statusName": first_text(detail.get("statusName"), detail.get("StatusName")) or None,
        "statusType": first_text(detail.get("statusType"), detail.get("StatusType")) or None,
        "progress": first_text(detail.get("progress"), detail.get("Progress")) or None,
        "createdBy": first_text(detail.get("createdBy"), detail.get("CreatedBy")) or None,
        "userExecStatus": executor_summary or None,
        "dateExec": first_text(detail.get("dateExec"), detail.get("DateExec")) or None,
    }


def status_summary(status: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": first_text(status.get("id"), status.get("Id")) or None,
        "name": status_name(status) or None,
        "projectId": first_text(status.get("projectId"), status.get("ProjectId")) or None,
        "userWorkflowId": status_workflow_id(status) or None,
        "kind": status_kind(status) or None,
        "isCurrentStatus": bool(status.get("isCurrentStatus")),
        "userUpdateStatus": first_text(status.get("userUpdateStatus")) or None,
        "theSameId": first_text(status.get("theSameId")) or None,
        "blockedByDefault": is_cancel_status(status),
    }


def status_preview_dict(prepared: PreparedStatusUpdate) -> dict[str, Any]:
    return {
        "workId": prepared.work_id,
        "current": task_summary(prepared.detail),
        "target": status_summary(prepared.target),
        "validations": prepared.validations,
        "update": {
            "method": "GET",
            "url": f"{WORK_ORIGIN}/api/Default/Work_UpdateStatusWork",
            "params": {
                "workId": prepared.work_id,
                "statusId": status_workflow_id(prepared.target),
            },
        },
    }


def progress_preview_dict(prepared: PreparedProgressUpdate) -> dict[str, Any]:
    current = task_summary(prepared.detail)
    return {
        "workId": prepared.work_id,
        "current": current,
        "target": {"progress": prepared.progress},
        "validations": prepared.validations,
        "update": {
            "method": "PUT",
            "url": f"{WORK_ORIGIN}/api/Default/Work_UpdateJsonData",
            "params": {
                "userWorkFlowId": prepared.work_id,
                "fileName": "Tiendo",
                "value": prepared.progress,
            },
        },
    }


def task_state_result_dict(
    operation: str,
    response: Any,
    preview: dict[str, Any],
    verified_detail: dict[str, Any],
    verified: bool,
) -> dict[str, Any]:
    return {
        "updated": True,
        "operation": operation,
        "verified": verified,
        "workId": preview["workId"],
        "current": preview["current"],
        "target": preview["target"],
        "verifiedTask": task_summary(verified_detail),
        "response": compact_response(response),
    }


def render_task_text(summary: dict[str, Any]) -> str:
    return "\n".join(
        [
            "Task BecaWork",
            f"- Task: {display_value(summary.get('title'))}",
            f"- Work ID: {display_value(summary.get('workId'))}",
            f"- Project: {display_value(summary.get('projectName'))}",
            f"- Project ID: {display_value(summary.get('projectId'))}",
            f"- Status: {display_value(summary.get('statusName'))} ({display_value(summary.get('statusId'))})",
            f"- Status type: {display_value(summary.get('statusType'))}",
            f"- Progress: {display_value(summary.get('progress'))}",
            f"- Created by: {display_value(summary.get('createdBy'))}",
            f"- User exec status: {display_value(summary.get('userExecStatus'))}",
            f"- Date exec: {display_value(summary.get('dateExec'))}",
        ]
    )


def render_statuses_text(statuses: list[dict[str, Any]]) -> str:
    lines = [f"{'STATUS ID':<12} {'CURRENT':<7} {'BLOCK':<7} {'KIND':<24} NAME"]
    for item in statuses:
        row = status_summary(item)
        lines.append(
            f"{display_value(row.get('userWorkflowId')):<12} "
            f"{str(row.get('isCurrentStatus')):<7} "
            f"{str(row.get('blockedByDefault')):<7} "
            f"{display_value(row.get('kind')):<24} "
            f"{display_value(row.get('name'))}"
        )
    return "\n".join(lines)


def render_preview_status_text(preview: dict[str, Any]) -> str:
    current = preview["current"]
    target = preview["target"]
    lines = [
        "Preview đổi status task",
        f"- Task: {display_value(current.get('title'))}",
        f"- Work ID: {display_value(preview.get('workId'))}",
        f"- Project: {display_value(current.get('projectName'))}",
        f"- Status hiện tại: {display_value(current.get('statusName'))} ({display_value(current.get('statusId'))})",
        f"- Status mới: {display_value(target.get('name'))} ({display_value(target.get('userWorkflowId'))})",
        f"- Loại status mới: {display_value(target.get('kind'))}",
    ]
    for validation in preview.get("validations", []):
        lines.append(f"- {validation.get('name')}: OK")
    return "\n".join(lines)


def render_preview_progress_text(preview: dict[str, Any]) -> str:
    current = preview["current"]
    target = preview["target"]
    lines = [
        "Preview đổi % done task",
        f"- Task: {display_value(current.get('title'))}",
        f"- Work ID: {display_value(preview.get('workId'))}",
        f"- Project: {display_value(current.get('projectName'))}",
        f"- Progress hiện tại: {display_value(current.get('progress'))}",
        f"- Progress mới: {display_value(target.get('progress'))}",
    ]
    for validation in preview.get("validations", []):
        lines.append(f"- {validation.get('name')}: OK")
    return "\n".join(lines)


def render_task_state_result_text(result: dict[str, Any]) -> str:
    target = result.get("target", {})
    verified_task = result.get("verifiedTask", {})
    if result.get("operation") == "update-status":
        target_text = f"{display_value(target.get('name'))} ({display_value(target.get('userWorkflowId'))})"
        verified_text = f"{display_value(verified_task.get('statusName'))} ({display_value(verified_task.get('statusId'))})"
    else:
        target_text = display_value(target.get("progress"))
        verified_text = display_value(verified_task.get("progress"))
    return "\n".join(
        [
            "Đã cập nhật task.",
            f"- Work ID: {display_value(result.get('workId'))}",
            f"- Target: {target_text}",
            f"- API verify: {'Thành công' if result.get('verified') else 'Chưa xác nhận được'}",
            f"- Giá trị hiện tại sau update: {verified_text}",
        ]
    )


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


def print_comments(rows: list[dict[str, Any]], as_json: bool) -> None:
    if as_json:
        print_json(rows)
        return
    print(f"{'WORK':<12} {'LAST MODIFIED':<20} {'BY':<24} COMMENT")
    for row in rows:
        print(
            f"{str(row['workId'] or ''):<12} {str(row['lastModified'] or ''):<20} "
            f"{str(row['author'] or ''):<24} {row['comment'] or ''}"
        )


def runtime_supported() -> bool:
    return sys.version_info[:2] >= MIN_PYTHON


def user_identity(user: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": user.get("id"),
        "fullName": user.get("fullName") or user.get("name"),
        "email": user.get("email"),
        "departmentId": user.get("departmentId") or user.get("department"),
    }


def active_task_args() -> argparse.Namespace:
    return argparse.Namespace(
        title=None,
        project_name=None,
        type="Xử lý",
        page=1,
        rows=200,
        mine_only=True,
        json=False,
    )


def command_version(_client: BecaClient, args: argparse.Namespace) -> int:
    result = {
        "version": CLI_VERSION,
        "configSchema": CONFIG_SCHEMA_VERSION,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "minimumPython": ".".join(map(str, MIN_PYTHON)),
    }
    if args.json:
        print_json(result)
    else:
        print(f"BecaWork Logtime {CLI_VERSION}")
        print(f"- Python: {result['python']} (minimum {result['minimumPython']})")
        print(f"- Platform: {result['platform']}")
        print(f"- Config schema: {CONFIG_SCHEMA_VERSION}")
    return 0


def command_setup(client: BecaClient, args: argparse.Namespace) -> int:
    if not runtime_supported():
        raise BecaError(
            f"BecaWork Logtime cần Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]} trở lên.",
            code="PYTHON_UNSUPPORTED",
            hint="Cài Python 3.11+ rồi chạy lại setup.",
        )
    try:
        existing = load_config()
    except BecaError as exc:
        if exc.code != "CONFIG_INVALID":
            raise
        existing = {}
    username = str(
        args.username
        or os.getenv("BECA_USERNAME")
        or existing.get("username")
        or secret_tool_lookup({"service": "becawork", "kind": "username"})
        or kwallet_lookup("username")
        or ""
    ).strip()
    if not username:
        if not sys.stdin.isatty():
            raise BecaError(
                "Setup cần terminal tương tác để nhập tài khoản.",
                code="SETUP_REQUIRES_TTY",
                hint=f"Mở terminal tương tác và chạy: {setup_command_text()}. Không dùng browser hoặc Computer Use.",
                exit_code=2,
                required_action="OPEN_INTERACTIVE_TERMINAL",
            )
        username = input("BecaWork username: ").strip()
    if not username:
        raise BecaError("Username không được để trống.", code="SETUP_INVALID")

    password = os.getenv("BECA_PASSWORD")
    source = "environment" if password else None
    if not password:
        password = keyring_lookup(username)
        source = "keyring" if password else None
    if not password:
        password = secret_tool_lookup(
            {"service": "becawork", "kind": "password", "username": username}
        ) or kwallet_lookup("password")
        source = "legacy-linux" if password else None
    entered_password = False
    if not password:
        if not sys.stdin.isatty():
            raise BecaError(
                "Setup cần terminal tương tác để nhập mật khẩu an toàn.",
                code="SETUP_REQUIRES_TTY",
                hint=f"Mở terminal tương tác và chạy: {setup_command_text()}. Không dùng browser hoặc Computer Use.",
                exit_code=2,
                required_action="OPEN_INTERACTIVE_TERMINAL",
            )
        password = getpass.getpass("BecaWork password: ")
        entered_password = True
        source = "interactive"
    if not password:
        raise BecaError("Password không được để trống.", code="SETUP_INVALID")

    client.login(username, password)
    user = client.authenticated_user or client.verify_login()
    contract = inspect_logtime_contract(client)
    tasks = list_tasks(client, active_task_args())

    credential_backend = "session" if entered_password else (source or "session")
    keyring_status = keyring_backend_status()
    keyring_result = dict(keyring_status)
    if entered_password and not args.no_store and keyring_status["available"]:
        remember = True
        if sys.stdin.isatty():
            answer = input("Lưu mật khẩu an toàn trong credential store? [Y/n]: ").strip().lower()
            remember = answer not in {"n", "no"}
        if remember:
            keyring_store(username, password)
            credential_backend = "keyring"
            keyring_result = {**keyring_status, "stored": True}
    elif entered_password and not args.no_store and not keyring_status["available"]:
        keyring_result = {**keyring_status, "stored": False}

    config_target = save_config(setup_config(username, credential_backend))
    result = {
        "ok": bool(contract.get("compatible")),
        "version": CLI_VERSION,
        "configPath": str(config_target),
        "credentialBackend": credential_backend,
        "keyring": keyring_result,
        "user": user_identity(user),
        "activeTaskCount": len(tasks),
        "contractCompatible": bool(contract.get("compatible")),
        "dataChanged": False,
    }
    if args.json:
        print_json(result)
    else:
        print("BecaWork setup")
        print(f"- Python: OK ({platform.python_version()})")
        print(f"- Platform: OK ({platform.system()})")
        print(f"- Login: OK ({result['user'].get('fullName') or result['user'].get('email') or username})")
        print(f"- Active tasks: {len(tasks)}")
        print(f"- API contract: {'OK' if result['contractCompatible'] else 'INCOMPATIBLE'}")
        if entered_password and not args.no_store and not keyring_status["available"]:
            print("- Credential: session only (install optional package 'keyring' to remember securely)")
        else:
            print(f"- Credential: {credential_backend}")
        print("Setup hoàn tất. Chưa có dữ liệu BecaWork nào được thay đổi.")
    return 0 if result["ok"] else 2


def command_doctor(client: BecaClient, args: argparse.Namespace) -> int:
    checks: list[dict[str, Any]] = []

    def add(name: str, status: str, message: str, hint: str | None = None) -> None:
        item: dict[str, Any] = {"name": name, "status": status, "message": message}
        if hint:
            item["hint"] = hint
        checks.append(item)

    if runtime_supported():
        add("python", "ok", f"Python {platform.python_version()}")
    else:
        add("python", "error", f"Python {platform.python_version()}", "Cài Python 3.11+.")
    add("platform", "ok", platform.platform())
    target = config_file()
    try:
        config = load_config(target)
        if config:
            add("config", "ok", str(target))
        else:
            add("config", "warning", "Chưa có cấu hình.", "Chạy setup.")
    except BecaError as exc:
        config = {}
        add("config", "error", str(exc), exc.hint)
    keyring_info = keyring_backend_status()
    add(
        "keyring",
        "ok" if keyring_info["available"] else "warning",
        keyring_info.get("message") or str(keyring_info.get("backend")),
        None if keyring_info["available"] else "Có thể cài package tùy chọn 'keyring'.",
    )
    try:
        response = client._open(
            Request(LOGIN_URL, headers=client._headers(target_url=LOGIN_URL))
        )
        response.read(1)
        add("network", "ok", "Kết nối được BecaWork SSO.")
    except BecaError as exc:
        add("network", "error", str(exc), exc.hint)

    authenticated = False
    try:
        client.ensure_auth(interactive=False)
        user = client.authenticated_user or client.verify_login()
        add("authentication", "ok", str(user_identity(user).get("email") or user_identity(user).get("fullName")))
        authenticated = True
    except BecaError as exc:
        status = "warning" if exc.code == "SETUP_REQUIRED" else "error"
        add("authentication", status, str(exc), exc.hint)
    if authenticated:
        try:
            contract = inspect_logtime_contract(client)
            add(
                "contract",
                "ok" if contract.get("compatible") else "error",
                "Logtime API tương thích." if contract.get("compatible") else "Logtime API không tương thích.",
                None if contract.get("compatible") else "Không submit cho tới khi contract được cập nhật.",
            )
        except BecaError as exc:
            add("contract", "error", str(exc), exc.hint)
    else:
        add("contract", "skipped", "Bỏ qua vì chưa xác thực.")

    ready = all(item["status"] not in {"error"} for item in checks) and authenticated
    result = {
        "ok": ready,
        "ready": ready,
        "version": CLI_VERSION,
        "configPath": str(target),
        "checks": checks,
        "dataChanged": False,
    }
    if args.debug:
        result["diagnostics"] = {
            "pythonExecutable": sys.executable,
            "pythonVersion": platform.python_version(),
            "platform": platform.platform(),
            "configExists": target.exists(),
            "authSource": client.auth_source,
            "workHost": urlparse(WORK_ORIGIN).netloc,
            "ssoHost": urlparse(SSO_ORIGIN).netloc,
        }
    if args.json:
        print_json(result)
    else:
        print("BecaWork doctor")
        symbols = {"ok": "OK", "warning": "WARN", "error": "ERROR", "skipped": "SKIP"}
        for item in checks:
            print(f"- [{symbols[item['status']]}] {item['name']}: {item['message']}")
            if item.get("hint"):
                print(f"  {item['hint']}")
        print("BecaWork đã sẵn sàng." if ready else "BecaWork chưa sẵn sàng. Chưa có dữ liệu nào được thay đổi.")
    return 0 if ready else 2


def command_whoami(client: BecaClient, args: argparse.Namespace) -> int:
    user = user_identity(client.authenticated_user or client.verify_login())
    if args.json:
        print_json(user)
    else:
        print("BecaWork account")
        print(f"- Name: {user.get('fullName') or '-'}")
        print(f"- Email: {user.get('email') or '-'}")
        print(f"- Department: {user.get('departmentId') or '-'}")
    return 0


def command_reset_auth(_client: BecaClient, args: argparse.Namespace) -> int:
    target = config_file()
    try:
        config = load_config(target)
    except BecaError:
        config = {}
    username = str(config.get("username") or "").strip()
    keyring_deleted = keyring_delete(username) if username else False
    existed = target.exists()
    try:
        target.unlink(missing_ok=True)
    except OSError as exc:
        raise BecaError(
            "Không thể xóa cấu hình BecaWork.",
            code="CONFIG_WRITE_FAILED",
            hint=f"Kiểm tra quyền tại {target}.",
        ) from exc
    result = {
        "ok": True,
        "configRemoved": existed,
        "keyringCredentialRemoved": keyring_deleted,
        "environmentVariablesChanged": False,
        "dataChanged": False,
    }
    if args.json:
        print_json(result)
    else:
        print("Đã xóa cấu hình đăng nhập cục bộ của BecaWork.")
        print("Biến môi trường và dữ liệu trên BecaWork không bị thay đổi.")
    return 0


def command_list_tasks(client: BecaClient, args: argparse.Namespace) -> int:
    tasks = list_tasks(client, args)
    print_tasks(tasks, args.json)
    return 0


def command_list_logtimes(client: BecaClient, args: argparse.Namespace) -> int:
    rows = list_logtimes(client, args)
    print_logtimes(rows, args.json)
    return 0


def command_list_comments(client: BecaClient, args: argparse.Namespace) -> int:
    rows = list_comments(client, args)
    print_comments(rows, args.json)
    return 0


def command_check_contracts(client: BecaClient, args: argparse.Namespace) -> int:
    result = inspect_logtime_contract(client)
    if args.json:
        print_json(result)
    else:
        print("BecaWork logtime contract")
        print(f"- Compatible: {'Yes' if result['compatible'] else 'No'}")
        print(f"- Form ID: {result['formId']} | Step ID: {result['stepId']}")
        hours = result["hours"]
        print(
            f"- Hours: {hours.get('type')}, min={hours.get('minimum')}, "
            f"max={hours.get('maximum')}, decimals={hours.get('numberOfDecimalPlaces')}"
        )
        print(f"- Actions: {', '.join(result['actions']) or '-'}")
        for issue in result["issues"]:
            print(f"- Issue: {issue}")
    return 0 if result["compatible"] else 2


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


def apply_progress_update(client: BecaClient, prepared: PreparedProgressUpdate) -> dict[str, Any]:
    preview = progress_preview_dict(prepared)
    response = client.put_json(
        "/api/Default/Work_UpdateJsonData",
        {},
        {
            "userWorkFlowId": prepared.work_id,
            "fileName": "Tiendo",
            "value": prepared.progress,
        },
    )
    verified_detail = get_task_detail(client, prepared.work_id)
    verified = progress_matches(task_summary(verified_detail).get("progress"), prepared.progress)
    return task_state_result_dict("update-progress", response, preview, verified_detail, verified)


def command_daily(client: BecaClient, args: argparse.Namespace) -> int:
    prepared = prepare_daily(client, args)
    preview = daily_preview_dict(prepared)
    print_json(preview) if getattr(args, "json", False) else print(render_daily_preview_text(preview))
    if not args.yes:
        confirmation = input("Save this BecaWork daily logtime batch? [y/N]: ").strip().lower()
        if confirmation not in {"y", "yes", "submit"}:
            raise BecaError("Daily submission cancelled.")

    results: list[dict[str, Any]] = []
    for item in prepared.entries:
        try:
            response = client.post_json(item.logtime.save_url, item.logtime.payload)
            verification = verify_saved_logtime(
                client,
                "submit",
                values_from_payload(item.logtime.payload),
            )
            logtime_result = save_result_dict("submit", response, verification)
            entry_result = daily_result_entry_dict(item, logtime_result=logtime_result)
            results.append(entry_result)
            if not verification["verified"]:
                failed = daily_result_entry_dict(
                    item,
                    logtime_result=logtime_result,
                    error="Saved, but API verification did not find the new logtime row.",
                )
                result = daily_result_dict(prepared, results, failed_entry=failed)
                print_json(result) if getattr(args, "json", False) else print(render_daily_result_text(result))
                return 2

            if item.progress:
                progress_result = apply_progress_update(client, item.progress)
                entry_result["progress"] = progress_result
                if not progress_result["verified"]:
                    failed = daily_result_entry_dict(
                        item,
                        logtime_result=logtime_result,
                        progress_result=progress_result,
                        error="Progress update saved, but API verification did not match the target value.",
                    )
                    result = daily_result_dict(prepared, results, failed_entry=failed)
                    print_json(result) if getattr(args, "json", False) else print(render_daily_result_text(result))
                    return 2
        except BecaError as exc:
            failed = daily_result_entry_dict(item, error=str(exc))
            result = daily_result_dict(prepared, results, failed_entry=failed)
            print_json(result) if getattr(args, "json", False) else print(render_daily_result_text(result))
            return 2

    result = daily_result_dict(prepared, results)
    print_json(result) if getattr(args, "json", False) else print(render_daily_result_text(result))
    return 0 if result["verified"] else 2


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
        "Mota": structured_description(
            args.description,
            getattr(args, "result", None),
            getattr(args, "blockers", None),
            getattr(args, "next_step", None),
        ) if args.description else None,
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


def command_get_task(client: BecaClient, args: argparse.Namespace) -> int:
    summary = task_summary(get_task_detail(client, str(args.work_id)))
    print_json(summary) if getattr(args, "json", False) else print(render_task_text(summary))
    return 0


def command_list_statuses(client: BecaClient, args: argparse.Namespace) -> int:
    detail = get_task_detail(client, str(args.work_id))
    project_id = first_text(detail.get("projectId"), detail.get("ProjectId"))
    if not project_id:
        raise BecaError("Task detail response did not include projectId.")
    statuses = get_next_statuses(client, str(args.work_id), project_id)
    rows = [status_summary(item) for item in statuses]
    print_json(rows) if getattr(args, "json", False) else print(render_statuses_text(statuses))
    return 0


def command_preview_status(client: BecaClient, args: argparse.Namespace) -> int:
    prepared = prepare_status_update(client, args)
    preview = status_preview_dict(prepared)
    print_json(preview) if getattr(args, "json", False) else print(render_preview_status_text(preview))
    return 0


def command_update_status(client: BecaClient, args: argparse.Namespace) -> int:
    prepared = prepare_status_update(client, args)
    preview = status_preview_dict(prepared)
    print_json(preview) if getattr(args, "json", False) else print(render_preview_status_text(preview))
    if not args.yes:
        confirmation = input("Type UPDATE to change this BecaWork task status: ").strip()
        if confirmation != "UPDATE":
            raise BecaError("Status update cancelled.")
    target_status_id = status_workflow_id(prepared.target)
    response = client.get_json(
        "/api/Default/Work_UpdateStatusWork",
        {"workId": prepared.work_id, "statusId": target_status_id},
    )
    verified_detail = get_task_detail(client, prepared.work_id)
    verified_summary = task_summary(verified_detail)
    verified = (
        verified_summary.get("statusId") == target_status_id
        or normalized_lookup_text(verified_summary.get("statusName")) == normalized_lookup_text(status_name(prepared.target))
    )
    result = task_state_result_dict("update-status", response, preview, verified_detail, verified)
    print_json(result) if getattr(args, "json", False) else print(render_task_state_result_text(result))
    return 0 if verified else 2


def command_preview_progress(client: BecaClient, args: argparse.Namespace) -> int:
    prepared = prepare_progress_update(client, args)
    preview = progress_preview_dict(prepared)
    print_json(preview) if getattr(args, "json", False) else print(render_preview_progress_text(preview))
    return 0


def command_update_progress(client: BecaClient, args: argparse.Namespace) -> int:
    prepared = prepare_progress_update(client, args)
    preview = progress_preview_dict(prepared)
    print_json(preview) if getattr(args, "json", False) else print(render_preview_progress_text(preview))
    if not args.yes:
        confirmation = input("Type UPDATE to change this BecaWork task progress: ").strip()
        if confirmation != "UPDATE":
            raise BecaError("Progress update cancelled.")
    result = apply_progress_update(client, prepared)
    print_json(result) if getattr(args, "json", False) else print(render_task_state_result_text(result))
    return 0 if result["verified"] else 2


def add_common_logtime_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--work-id", required=True)
    parser.add_argument("--date", help="YYYY-MM-DD; defaults to previous business day")
    parser.add_argument("--hours", default="8")
    parser.add_argument("--action", default="Thực hiện")
    parser.add_argument("--description", required=True, help="Đã thực hiện; HTML is accepted for legacy callers")
    parser.add_argument("--result", help="Kết quả")
    parser.add_argument("--blockers", help="Vướng mắc")
    parser.add_argument("--next-step", dest="next_step", help="Bước tiếp theo")
    parser.add_argument("--allow-duplicate", action="store_true")


def add_update_logtime_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--log-id", required=True)
    parser.add_argument("--date", help="YYYY-MM-DD; defaults to the existing logtime date")
    parser.add_argument("--hours")
    parser.add_argument("--action")
    parser.add_argument("--description")
    parser.add_argument("--result", help="Kết quả; requires --description")
    parser.add_argument("--blockers", help="Vướng mắc; requires --description")
    parser.add_argument("--next-step", dest="next_step", help="Bước tiếp theo; requires --description")
    parser.add_argument("--allow-duplicate", action="store_true")


def add_verify_logtime_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--work-id", required=True)
    parser.add_argument("--date", help="YYYY-MM-DD; defaults to previous business day")
    parser.add_argument("--project-id")
    parser.add_argument("--log-user-workflow-id")
    parser.add_argument("--hours")
    parser.add_argument("--description")
    parser.add_argument("--result")
    parser.add_argument("--blockers")
    parser.add_argument("--next-step", dest="next_step")


def add_status_target_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--work-id", required=True)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--status", help="Target status name from Work_GetNextStatus, e.g. In Progress")
    target.add_argument("--status-id", help="Target status userWorkflowId from Work_GetNextStatus")
    parser.add_argument("--allow-cancel", action="store_true", help="Allow Pending/Reject/cancel statuses")


def add_progress_target_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--work-id", required=True)
    parser.add_argument("--progress", required=True, help="Percent complete, e.g. 28 or 28%%")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Set up and manage BecaWork logtime/task state.")
    parser.add_argument("--cookie", default=os.getenv("BECA_COOKIE"), help="BecaWork cookie header value")
    subparsers = parser.add_subparsers(dest="command", required=True)

    setup_parser = subparsers.add_parser("setup", help="Set up BecaWork safely on this computer")
    setup_parser.add_argument("--username", help="BecaWork username; password is always read securely")
    setup_parser.add_argument("--no-store", action="store_true", help="Do not store password in the OS credential store")
    setup_parser.add_argument("--json", action="store_true")
    setup_parser.set_defaults(func=command_setup)

    doctor_parser = subparsers.add_parser("doctor", help="Diagnose runtime, network, login, and API compatibility")
    doctor_parser.add_argument("--json", action="store_true")
    doctor_parser.add_argument("--debug", action="store_true", help="Include redacted diagnostic metadata")
    doctor_parser.set_defaults(func=command_doctor)

    whoami_parser = subparsers.add_parser("whoami", help="Show the authenticated BecaWork account")
    whoami_parser.add_argument("--json", action="store_true")
    whoami_parser.set_defaults(func=command_whoami)

    reset_parser = subparsers.add_parser("reset-auth", help="Remove local BecaWork authentication settings")
    reset_parser.add_argument("--json", action="store_true")
    reset_parser.set_defaults(func=command_reset_auth)

    version_parser = subparsers.add_parser("version", help="Show BecaWork Logtime version and runtime")
    version_parser.add_argument("--json", action="store_true")
    version_parser.set_defaults(func=command_version)

    list_parser = subparsers.add_parser("list-tasks", help="List active tasks from BecaWork")
    list_parser.add_argument("--title")
    list_parser.add_argument("--project-name")
    list_parser.add_argument("--type", default="Xử lý")
    list_parser.add_argument("--page", type=int, default=1)
    list_parser.add_argument("--rows", type=int, default=15)
    list_parser.add_argument("--mine-only", action="store_true")
    list_parser.add_argument("--json", action="store_true")
    list_parser.set_defaults(func=command_list_tasks)

    contracts_parser = subparsers.add_parser(
        "check-contracts",
        help="Read current logtime form metadata and fail if the supported contract changed",
    )
    contracts_parser.add_argument("--json", action="store_true")
    contracts_parser.set_defaults(func=command_check_contracts)

    logtimes_parser = subparsers.add_parser("list-logtimes", help="List personal logtime rows for a day")
    logtimes_parser.add_argument("--date", help="YYYY-MM-DD; defaults to previous business day")
    logtimes_parser.add_argument("--project-id", default="-1")
    logtimes_parser.add_argument("--department", help="Department id; defaults to current user's department")
    logtimes_parser.add_argument("--json", action="store_true")
    logtimes_parser.set_defaults(func=command_list_logtimes)

    comments_parser = subparsers.add_parser(
        "list-comments",
        help="List comments added or modified since a date on active assigned tasks",
    )
    comments_parser.add_argument("--since", help="YYYY-MM-DD; defaults to previous business day")
    comments_parser.add_argument("--work-id", help="Read one task instead of all active assigned tasks")
    comments_parser.add_argument("--type", default="Xử lý")
    comments_parser.add_argument("--rows", type=int, default=200)
    comments_parser.add_argument(
        "--show-sensitive",
        action="store_true",
        help="Disable default credential/token redaction in comment text",
    )
    comments_parser.add_argument("--json", action="store_true")
    comments_parser.set_defaults(func=command_list_comments)

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

    daily_parser = subparsers.add_parser("daily", help="Submit a full 8h daily logtime batch after one preview")
    daily_parser.add_argument(
        "--entry",
        action="append",
        default=[],
        help='Use "task | hours | description [| result=... | blockers=... | next=... | progress=28]"',
    )
    daily_parser.add_argument("--date", help="YYYY-MM-DD; defaults to previous business day")
    daily_parser.add_argument("--allow-duplicate", action="store_true")
    daily_parser.add_argument("--yes", action="store_true", help="Skip prompt only when the user explicitly requested submit")
    daily_parser.add_argument("--json", action="store_true", help="Print machine-readable JSON instead of review text")
    daily_parser.set_defaults(func=command_daily)

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

    get_task_parser = subparsers.add_parser("get-task", help="Read one BecaWork task by work id")
    get_task_parser.add_argument("--work-id", required=True)
    get_task_parser.add_argument("--json", action="store_true", help="Print machine-readable JSON instead of review text")
    get_task_parser.set_defaults(func=command_get_task)

    statuses_parser = subparsers.add_parser("list-statuses", help="List current and next task statuses")
    statuses_parser.add_argument("--work-id", required=True)
    statuses_parser.add_argument("--json", action="store_true", help="Print machine-readable JSON instead of review text")
    statuses_parser.set_defaults(func=command_list_statuses)

    preview_status_parser = subparsers.add_parser("preview-status", help="Preview and validate a task status change")
    add_status_target_args(preview_status_parser)
    preview_status_parser.add_argument("--json", action="store_true", help="Print machine-readable JSON instead of review text")
    preview_status_parser.set_defaults(func=command_preview_status)

    update_status_parser = subparsers.add_parser("update-status", help="Update a task status after explicit confirmation")
    add_status_target_args(update_status_parser)
    update_status_parser.add_argument("--yes", action="store_true", help="Skip prompt only when the user explicitly requested update")
    update_status_parser.add_argument("--json", action="store_true", help="Print machine-readable JSON instead of review text")
    update_status_parser.set_defaults(func=command_update_status)

    preview_progress_parser = subparsers.add_parser("preview-progress", help="Preview and validate a task progress change")
    add_progress_target_args(preview_progress_parser)
    preview_progress_parser.add_argument("--json", action="store_true", help="Print machine-readable JSON instead of review text")
    preview_progress_parser.set_defaults(func=command_preview_progress)

    update_progress_parser = subparsers.add_parser("update-progress", help="Update task percent complete after explicit confirmation")
    add_progress_target_args(update_progress_parser)
    update_progress_parser.add_argument("--yes", action="store_true", help="Skip prompt only when the user explicitly requested update")
    update_progress_parser.add_argument("--json", action="store_true", help="Print machine-readable JSON instead of review text")
    update_progress_parser.set_defaults(func=command_update_progress)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not runtime_supported() and args.command not in {"doctor", "version"}:
        raise BecaError(
            f"BecaWork Logtime cần Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]} trở lên.",
            code="PYTHON_UNSUPPORTED",
            hint="Cài Python 3.11+ rồi thử lại.",
        )
    client = BecaClient(cookie=args.cookie)
    if args.command not in {"setup", "doctor", "reset-auth", "version"}:
        try:
            client.ensure_auth(interactive=False)
        except BecaError as exc:
            if exc.code != "SETUP_REQUIRED" or not sys.stdin.isatty():
                raise
            print("BecaWork chưa được thiết lập. Bắt đầu onboarding read-only...")
            command_setup(
                client,
                argparse.Namespace(username=None, no_store=False, json=False),
            )
    return args.func(client, args)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BecaError as exc:
        if "--json" in sys.argv:
            print(json.dumps(exc.as_dict(), ensure_ascii=False, indent=2), file=sys.stderr)
        else:
            print(f"error [{exc.code}]: {exc}", file=sys.stderr)
            if exc.hint:
                print(f"hint: {exc.hint}", file=sys.stderr)
            if exc.required_action:
                print(f"required action: {exc.required_action}", file=sys.stderr)
            print(f"data changed: {'yes' if exc.data_changed else 'no'}", file=sys.stderr)
        raise SystemExit(exc.exit_code)
