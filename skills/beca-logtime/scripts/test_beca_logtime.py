#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import io
import json
import pathlib
import sys
import tempfile
import unittest
from argparse import Namespace
from contextlib import redirect_stdout
from datetime import date
from unittest.mock import patch


SCRIPT = pathlib.Path(__file__).with_name("beca_logtime.py")
FIXTURES = SCRIPT.parent.parent / "tests" / "fixtures"
SPEC = importlib.util.spec_from_file_location("beca_logtime", SCRIPT)
assert SPEC and SPEC.loader
beca_logtime = importlib.util.module_from_spec(SPEC)
sys.modules["beca_logtime"] = beca_logtime
SPEC.loader.exec_module(beca_logtime)


class BecaLogtimeTests(unittest.TestCase):
    def test_v2_requires_python_311_and_uses_fixed_vietnam_timezone(self) -> None:
        self.assertEqual(beca_logtime.MIN_PYTHON, (3, 11))
        self.assertEqual(beca_logtime.LOCAL_TIMEZONE.utcoffset(None).total_seconds(), 7 * 3600)

    def test_config_paths_are_platform_native(self) -> None:
        home = pathlib.Path("/Users/demo")
        windows = beca_logtime.config_dir(
            "win32", {"APPDATA": r"C:\Users\demo\AppData\Roaming"}, pathlib.Path("C:/Users/demo")
        )
        macos = beca_logtime.config_dir("darwin", {}, home)
        linux = beca_logtime.config_dir("linux", {"XDG_CONFIG_HOME": "/tmp/config"}, pathlib.Path("/home/demo"))
        self.assertEqual(windows.name, "beca-logtime")
        self.assertIn("AppData", str(windows.parent))
        self.assertEqual(macos, home / "Library" / "Application Support" / "beca-logtime")
        self.assertEqual(linux, pathlib.Path("/tmp/config/beca-logtime"))

    def test_config_round_trip_contains_no_secret(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = pathlib.Path(directory) / "config.json"
            value = beca_logtime.setup_config("demo", "keyring")
            beca_logtime.save_config(value, target)
            loaded = beca_logtime.load_config(target)
            self.assertEqual(loaded["username"], "demo")
            raw = target.read_text(encoding="utf-8").casefold()
            self.assertNotIn("password", raw)
            self.assertNotIn("cookie", raw)
            self.assertNotIn("token", raw)

    def test_error_json_has_stable_safe_shape(self) -> None:
        error = beca_logtime.BecaError(
            "Login failed", code="AUTH_INVALID", hint="Try again"
        ).as_dict()
        self.assertEqual(error["error"]["code"], "AUTH_INVALID")
        self.assertFalse(error["error"]["dataChanged"])

    def test_form_parser_scopes_inputs_to_each_form(self) -> None:
        forms = beca_logtime.parse_forms(
            '<form action="/Account/Login"><input name="Input.Password" type="password"></form>'
            '<form action="https://work.becawork.vn/signin-oidc">'
            '<input name="code" value="demo"><input name="state" value="state-demo"></form>'
        )
        self.assertEqual(len(forms), 2)
        self.assertIn("Input.Password", forms[0].inputs)
        self.assertNotIn("code", forms[0].inputs)
        self.assertTrue(beca_logtime.is_callback_form(forms[1], beca_logtime.SSO_ORIGIN))

    def test_failed_login_page_is_not_treated_as_callback(self) -> None:
        page = (FIXTURES / "login-invalid-password.html").read_text(encoding="utf-8")
        forms = beca_logtime.parse_forms(page)
        self.assertTrue(beca_logtime.is_login_form(forms[0], beca_logtime.LOGIN_URL))
        self.assertFalse(beca_logtime.is_callback_form(forms[0], beca_logtime.LOGIN_URL))
        self.assertEqual(beca_logtime.safe_auth_message(page), "Tài khoản hoặc mật khẩu chưa đúng.")

    def test_auth_challenge_is_detected(self) -> None:
        page = (FIXTURES / "login-mfa.html").read_text(encoding="utf-8")
        self.assertTrue(beca_logtime.is_auth_challenge(page, beca_logtime.parse_forms(page)))

    def test_login_success_submits_oidc_callback_and_verifies_identity(self) -> None:
        class Response:
            def __init__(self, body: str, url: str) -> None:
                self.body = body.encode()
                self.url = url

            def read(self, _size=None):
                return self.body

            def geturl(self):
                return self.url

        class LoginClient(beca_logtime.BecaClient):
            def __init__(self) -> None:
                super().__init__()
                self.requests = []
                self.responses = [
                    Response(
                        (FIXTURES / "login-page.html").read_text(encoding="utf-8"),
                        beca_logtime.LOGIN_URL,
                    ),
                    Response(
                        (FIXTURES / "login-success-callback.html").read_text(encoding="utf-8"),
                        beca_logtime.LOGIN_URL,
                    ),
                    Response("<html>ok</html>", "https://work.becawork.vn/"),
                    Response(
                        json.dumps({"id": 100, "email": "demo@example.com", "fullName": "Demo"}),
                        "https://work.becawork.vn/api/Default/Work_GetInfLogin",
                    ),
                ]

            def _open(self, request):
                self.requests.append(request)
                return self.responses.pop(0)

        client = LoginClient()
        client.login("demo", "password-demo")
        self.assertEqual(client.authenticated_user["email"], "demo@example.com")
        self.assertEqual(client.requests[2].full_url, "https://work.becawork.vn/signin-oidc")
        self.assertNotIn("password-demo", client.requests[2].data.decode())

    def test_login_rejection_returns_auth_invalid_instead_of_callback_error(self) -> None:
        class Response:
            def __init__(self, fixture: str) -> None:
                self.body = (FIXTURES / fixture).read_bytes()

            def read(self, _size=None):
                return self.body

            def geturl(self):
                return beca_logtime.LOGIN_URL

        class RejectedClient(beca_logtime.BecaClient):
            def __init__(self) -> None:
                super().__init__()
                self.responses = [Response("login-page.html"), Response("login-invalid-password.html")]

            def _open(self, _request):
                return self.responses.pop(0)

        with self.assertRaises(beca_logtime.BecaError) as raised:
            RejectedClient().login("demo", "wrong-password")
        self.assertEqual(raised.exception.code, "AUTH_INVALID")
        self.assertFalse(raised.exception.data_changed)

    def test_login_mfa_returns_explicit_challenge_error(self) -> None:
        class Response:
            def __init__(self, fixture: str) -> None:
                self.body = (FIXTURES / fixture).read_bytes()

            def read(self, _size=None):
                return self.body

            def geturl(self):
                return beca_logtime.LOGIN_URL

        class MfaClient(beca_logtime.BecaClient):
            def __init__(self) -> None:
                super().__init__()
                self.responses = [Response("login-page.html"), Response("login-mfa.html")]

            def _open(self, _request):
                return self.responses.pop(0)

        with self.assertRaises(beca_logtime.BecaError) as raised:
            MfaClient().login("demo", "password-demo")
        self.assertEqual(raised.exception.code, "AUTH_CHALLENGE_REQUIRED")

    def test_new_cli_commands_are_discoverable(self) -> None:
        parser = beca_logtime.build_parser()
        self.assertIs(parser.parse_args(["setup", "--no-store"]).func, beca_logtime.command_setup)
        self.assertIs(parser.parse_args(["doctor", "--json"]).func, beca_logtime.command_doctor)
        self.assertIs(parser.parse_args(["whoami"]).func, beca_logtime.command_whoami)
        self.assertIs(parser.parse_args(["reset-auth"]).func, beca_logtime.command_reset_auth)
        self.assertIs(parser.parse_args(["version"]).func, beca_logtime.command_version)

    def test_setup_with_environment_credentials_is_read_only_and_writes_safe_config(self) -> None:
        class SetupClient:
            authenticated_user = None

            def __init__(self) -> None:
                self.calls = []

            def login(self, username, password):
                self.calls.append(("login", username, password))
                self.authenticated_user = {
                    "id": 100,
                    "email": "demo@example.com",
                    "fullName": "Demo User",
                    "departmentId": 5,
                }

            def verify_login(self):
                return self.authenticated_user

        with tempfile.TemporaryDirectory() as directory:
            client = SetupClient()
            environment = {
                "BECA_USERNAME": "demo",
                "BECA_PASSWORD": "password-demo",
                "BECA_CONFIG_DIR": directory,
            }
            with patch.dict(beca_logtime.os.environ, environment, clear=False), patch.object(
                beca_logtime, "inspect_logtime_contract", return_value={"compatible": True}
            ), patch.object(beca_logtime, "list_tasks", return_value=[{"id": 1}]):
                with redirect_stdout(io.StringIO()):
                    result = beca_logtime.command_setup(
                        client,
                        Namespace(username=None, no_store=True, json=True),
                    )
            self.assertEqual(result, 0)
            self.assertEqual(client.calls, [("login", "demo", "password-demo")])
            saved = json.loads((pathlib.Path(directory) / "config.json").read_text())
            self.assertEqual(saved["username"], "demo")
            self.assertNotIn("password", json.dumps(saved).casefold())

    def test_reset_auth_recovers_from_invalid_config(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = pathlib.Path(directory) / "config.json"
            target.write_text("not-json", encoding="utf-8")
            with patch.dict(beca_logtime.os.environ, {"BECA_CONFIG_DIR": directory}, clear=False):
                with redirect_stdout(io.StringIO()):
                    result = beca_logtime.command_reset_auth(
                        object(), Namespace(json=True)
                    )
            self.assertEqual(result, 0)
            self.assertFalse(target.exists())

    def test_doctor_is_read_only_and_reports_ready(self) -> None:
        class Response:
            def read(self, _size=None):
                return b"<html>login</html>"

        class DoctorClient:
            auth_source = "environment"
            authenticated_user = {
                "id": 100,
                "email": "demo@example.com",
                "fullName": "Demo User",
            }

            def __init__(self) -> None:
                self.calls = []

            def _headers(self, headers=None, target_url=None):
                return {"Accept": "application/json"}

            def _open(self, request):
                self.calls.append(("GET", request.full_url))
                return Response()

            def ensure_auth(self, interactive=False):
                self.calls.append(("AUTH", interactive))

            def verify_login(self):
                return self.authenticated_user

        client = DoctorClient()
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            beca_logtime.os.environ, {"BECA_CONFIG_DIR": directory}, clear=False
        ), patch.object(
            beca_logtime, "load_config", return_value={"schemaVersion": 1, "username": "demo"}
        ), patch.object(
            beca_logtime, "keyring_backend_status", return_value={"available": True, "backend": "TestKeyring", "message": None}
        ), patch.object(
            beca_logtime, "inspect_logtime_contract", return_value={"compatible": True}
        ):
            output = io.StringIO()
            with redirect_stdout(output):
                result = beca_logtime.command_doctor(
                    client, Namespace(json=True, debug=True)
                )
        self.assertEqual(result, 0)
        report = json.loads(output.getvalue())
        self.assertTrue(report["ready"])
        self.assertFalse(report["dataChanged"])
        self.assertFalse(any(method in {"POST", "PUT", "PATCH", "DELETE"} for method, *_ in client.calls))

    def test_previous_business_day_skips_weekend(self) -> None:
        self.assertEqual(
            beca_logtime.previous_business_day(date(2026, 7, 6)),
            date(2026, 7, 3),
        )
        self.assertEqual(
            beca_logtime.previous_business_day(date(2026, 7, 1)),
            date(2026, 6, 30),
        )

    def test_html_description_wraps_plain_text_only(self) -> None:
        self.assertEqual(beca_logtime.html_description("Fix API"), "<p>Fix API</p>")
        self.assertEqual(beca_logtime.html_description("<p>Fix API</p>"), "<p>Fix API</p>")

    def test_structured_description_uses_current_form_sections_and_escapes_text(self) -> None:
        value = beca_logtime.structured_description(
            "Fix API <done>", "Passed", "None", "Deploy & monitor"
        )
        self.assertIn("<em>Đã thực hiện</em>: Fix API &lt;done&gt;", value)
        self.assertIn("<em>Kết quả</em>: Passed", value)
        self.assertIn("<em>Vướng mắc</em>: None", value)
        self.assertIn("<em>Bước tiếp theo</em>: Deploy &amp; monitor", value)

    def test_validate_hours_matches_backend_integer_range(self) -> None:
        self.assertEqual(beca_logtime.validate_hours("1"), "1")
        self.assertEqual(beca_logtime.validate_hours("16"), "16")
        for invalid in ("0", "0.5", "17", "nope"):
            with self.subTest(invalid=invalid):
                with self.assertRaises(beca_logtime.BecaError):
                    beca_logtime.validate_hours(invalid)

    def test_response_data_accepts_extended_envelope_but_not_task_data_field(self) -> None:
        self.assertEqual(
            beca_logtime.response_data({"data": [1], "status": 200, "traceId": "abc", "newMeta": True}),
            [1],
        )
        task = {"data": None, "status": "1330833", "title": "Task"}
        self.assertIs(beca_logtime.response_data(task), task)

    def test_normalize_user_id(self) -> None:
        self.assertEqual(beca_logtime.normalize_user_id("P:10000"), "P:10000")
        self.assertEqual(beca_logtime.normalize_user_id("10000"), "P:10000")

    def test_normalize_date_value(self) -> None:
        self.assertEqual(beca_logtime.normalize_date_value("2026-07-01 00:00:00"), "2026-07-01")
        self.assertEqual(beca_logtime.normalize_date_value("01/07/2026"), "2026-07-01")
        self.assertEqual(beca_logtime.normalize_date_value("2026/07/01"), "2026-07-01")

    def test_number_or_none(self) -> None:
        self.assertEqual(beca_logtime.number_or_none("-8"), -8.0)
        self.assertEqual(beca_logtime.number_or_none(0), 0.0)
        self.assertIsNone(beca_logtime.number_or_none("not-a-number"))

    def test_api_number_text_removes_integer_float_suffix(self) -> None:
        self.assertEqual(beca_logtime.api_number_text(8.0), "8")
        self.assertEqual(beca_logtime.api_number_text("2.5"), "2.5")

    def test_flatten_tasks_preserves_parent_title(self) -> None:
        tasks = beca_logtime.flatten_tasks(
            [
                {
                    "title": "Parent",
                    "userWorkflowId": "1",
                    "children": [{"title": "Child", "userWorkflowId": "2"}],
                }
            ]
        )
        self.assertEqual([task["title"] for task in tasks], ["Parent", "Child"])
        self.assertEqual(tasks[1]["parentTitle"], "Parent")

    def test_list_comments_filters_since_date_and_non_mine_tasks(self) -> None:
        class CommentClient:
            def get_json(self, path, params=None):
                if path == "/api/Default/Work_GetWorkInProcess":
                    return [
                        {
                            "userWorkflowId": "100",
                            "title": "Mine",
                            "isMyWork": True,
                        },
                        {
                            "userWorkflowId": "200",
                            "title": "Not mine",
                            "isMyWork": False,
                        },
                    ]
                if path == "/api/Default/Work_GetComment":
                    self.assert_work_id = (params or {}).get("workId")
                    return [
                        {
                            "id": 2,
                            "workId": "100",
                            "workName": "Mine",
                            "fullName": "BA User",
                            "note": "<p>Logic mới</p>",
                            "lastModified": "2026-07-16T08:00:00",
                        },
                        {
                            "id": 1,
                            "workId": "100",
                            "note": "Comment cũ",
                            "lastModified": "2026-07-14T08:00:00",
                        },
                    ]
                raise AssertionError(path)

        client = CommentClient()
        rows = beca_logtime.list_comments(
            client,
            Namespace(since="2026-07-15", work_id=None, type="Xử lý", rows=200),
        )
        self.assertEqual(client.assert_work_id, "100")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["commentId"], 2)
        self.assertEqual(rows[0]["comment"], "Logic mới")

    def test_list_comments_parser_defaults_to_assigned_tasks(self) -> None:
        args = beca_logtime.build_parser().parse_args(
            ["list-comments", "--since", "2026-07-15"]
        )
        self.assertIsNone(args.work_id)
        self.assertEqual(args.rows, 200)
        self.assertIs(args.func, beca_logtime.command_list_comments)

    def test_object_to_fields_uses_none_for_empty_values(self) -> None:
        self.assertEqual(
            beca_logtime.object_to_fields({"A": "", "B": 8}),
            [{"name": "A", "value": None}, {"name": "B", "value": "8"}],
        )

    def test_post_json_adds_xsrf_header(self) -> None:
        class HeaderClient(beca_logtime.BecaClient):
            def __init__(self) -> None:
                super().__init__(cookie="sid=abc")
                self.captured = {}

            def xsrf_token(self) -> str:
                return "token-123"

            def _request_text(self, url, data=None, headers=None, method="GET"):
                self.captured = {"url": url, "data": data, "headers": headers, "method": method}
                return "{}"

        client = HeaderClient()
        client.post_json("/api/test", {"ok": True})
        self.assertEqual(client.captured["method"], "POST")
        self.assertEqual(client.captured["headers"]["X-XSRF-TOKEN"], "token-123")
        self.assertEqual(client.captured["headers"]["Origin"], beca_logtime.WORK_ORIGIN)

    def test_put_json_adds_xsrf_header(self) -> None:
        class HeaderClient(beca_logtime.BecaClient):
            def __init__(self) -> None:
                super().__init__(cookie="sid=abc")
                self.captured = {}

            def xsrf_token(self) -> str:
                return "token-123"

            def _request_text(self, url, data=None, headers=None, method="GET"):
                self.captured = {"url": url, "data": data, "headers": headers, "method": method}
                return "{}"

        client = HeaderClient()
        client.put_json("/api/test", {"ok": True})
        self.assertEqual(client.captured["method"], "PUT")
        self.assertEqual(client.captured["headers"]["X-XSRF-TOKEN"], "token-123")
        self.assertEqual(client.captured["headers"]["Origin"], beca_logtime.WORK_ORIGIN)

    def test_cross_host_redirect_strips_explicit_cookie_header(self) -> None:
        request = beca_logtime.Request(
            "https://work.becawork.vn/api/test",
            headers={"Cookie": "sid=secret"},
        )
        redirected = beca_logtime.SameOriginRedirectHandler().redirect_request(
            request,
            None,
            302,
            "Found",
            {},
            "https://sso.becawork.vn/Account/Login",
        )
        self.assertIsNotNone(redirected)
        self.assertIsNone(redirected.get_header("Cookie"))

    def test_explicit_cookie_is_only_attached_to_work_host(self) -> None:
        client = beca_logtime.BecaClient(cookie="sid=secret")
        work_headers = client._headers(target_url="https://work.becawork.vn/api/test")
        sso_headers = client._headers(target_url="https://sso.becawork.vn/Account/Login")
        self.assertEqual(work_headers.get("Cookie"), "sid=secret")
        self.assertNotIn("Cookie", sso_headers)

    def test_prepare_blocks_negative_over_logtime_before_duplicate_check(self) -> None:
        client = FakeClient(over="-8", logs=[{"Ngay": "2026-07-01 00:00:00"}])
        with self.assertRaisesRegex(beca_logtime.BecaError, "Over-logtime"):
            beca_logtime.prepare_logtime(client, make_args())
        self.assertNotIn("GET:/api/Default/Work_GetLogtimeByWorkId", client.calls)

    def test_prepare_blocks_duplicate_same_day(self) -> None:
        client = FakeClient(over=0, logs=[{"id": 7, "Ngay": "2026-07-01 00:00:00"}])
        with self.assertRaisesRegex(beca_logtime.BecaError, "existing logtime"):
            beca_logtime.prepare_logtime(client, make_args())

    def test_prepare_rejects_action_not_in_live_form(self) -> None:
        client = FakeClient(over=0, logs=[])
        with self.assertRaisesRegex(beca_logtime.BecaError, "Allowed values"):
            beca_logtime.prepare_logtime(client, make_args(action="Unknown"))

    def test_submit_validates_before_save(self) -> None:
        client = FakeClient(over=0, logs=[])
        args = make_args(yes=True, json=True)
        with redirect_stdout(io.StringIO()):
            result = beca_logtime.command_submit(client, args)
        self.assertEqual(result, 0)
        validate_index = client.calls.index("GET:/api/Default/Work_CheckOverInLogtime")
        save_index = next(
            index
            for index, call in enumerate(client.calls)
            if call.startswith("POST:/api/ApiEoffice/Eoffice_ValidateAndInsertData")
        )
        verify_index = max(
            index
            for index, call in enumerate(client.calls)
            if call == "GET:/api/Default/Work_GetLogtimeByWorkId"
        )
        self.assertLess(validate_index, save_index)
        self.assertGreater(verify_index, save_index)

    def test_submit_verify_failure_reports_saved_but_unverified(self) -> None:
        client = FakeClient(over=0, logs=[], save_visible=False)
        args = make_args(yes=True, json=True)
        output = io.StringIO()
        with redirect_stdout(output):
            result = beca_logtime.command_submit(client, args)
        self.assertEqual(result, 2)
        last = last_json_object(output.getvalue())
        self.assertTrue(last["saved"])
        self.assertFalse(last["verified"])

    def test_prepare_update_preserves_existing_project_work_user_fields(self) -> None:
        client = FakeClient(over=2, logs=[])
        prepared = beca_logtime.prepare_update_logtime(
            client,
            make_update_args(hours="6", description="Updated work"),
        )
        values = json_from_payload(prepared.payload)
        self.assertEqual(values["Duan"], "1330830")
        self.assertEqual(values["Congviec"], "1385175")
        self.assertEqual(values["Nguoilap"], "Demo User")
        self.assertEqual(values["Email"], "user@example.com")
        self.assertEqual(values["UserId"], "P:10000")
        self.assertEqual(values["SoGio"], "6")
        self.assertIn("<em>Đã thực hiện</em>: Updated work", values["Mota"])
        self.assertIn("<em>Kết quả</em>:", values["Mota"])

    def test_update_uses_log_user_workflow_id_in_endpoint(self) -> None:
        client = FakeClient(over=2, logs=[])
        with redirect_stdout(io.StringIO()):
            result = beca_logtime.command_update(client, make_update_args(hours="6", yes=True, json=True))
        self.assertEqual(result, 0)
        put_call = next(call for call in client.calls if call.startswith("PUT:"))
        self.assertIn("Eoffice_UpdateData", put_call)
        self.assertIn("workId=1422459", put_call)

    def test_update_validates_before_put(self) -> None:
        client = FakeClient(over=2, logs=[])
        with redirect_stdout(io.StringIO()):
            beca_logtime.command_update(client, make_update_args(hours="6", yes=True, json=True))
        validate_index = client.calls.index("GET:/api/Default/Work_CheckOverInLogtime")
        update_index = next(index for index, call in enumerate(client.calls) if call.startswith("PUT:"))
        self.assertLess(validate_index, update_index)

    def test_update_verifies_by_user_workflow_id_not_old_log_id(self) -> None:
        client = FakeClient(over=2, logs=[], updated_log_id=21041)
        output = io.StringIO()
        with redirect_stdout(output):
            result = beca_logtime.command_update(client, make_update_args(hours="6", yes=True, json=True))
        self.assertEqual(result, 0)
        last = last_json_object(output.getvalue())
        self.assertTrue(last["verified"])
        self.assertEqual(last["logtime"]["matchedLogId"], "21041")
        self.assertEqual(last["logtime"]["logUserWorkflowId"], "1422459")

    def test_preview_defaults_to_human_readable_text(self) -> None:
        client = FakeClient(over=0, logs=[])
        output = io.StringIO()
        with redirect_stdout(output):
            beca_logtime.command_preview(client, make_args())
        text = output.getvalue()
        self.assertIn("Preview logtime mới", text)
        self.assertIn("- Work ID: 1415256", text)
        self.assertIn("- Mô tả: Đã thực hiện: Worked on Public Wifi backend", text)
        self.assertIn("Kết quả:", text)
        self.assertFalse(text.lstrip().startswith("{"))

    def test_submit_defaults_to_human_readable_text_after_save(self) -> None:
        client = FakeClient(over=0, logs=[])
        output = io.StringIO()
        with redirect_stdout(output):
            result = beca_logtime.command_submit(client, make_args(yes=True))
        self.assertEqual(result, 0)
        text = output.getvalue()
        self.assertIn("Preview logtime mới", text)
        self.assertIn("Đã tạo mới logtime.", text)
        self.assertIn("API verify: Thành công", text)
        self.assertFalse(text.lstrip().startswith("{"))

    def test_update_unchanged_date_uses_existing_hours_as_old_val(self) -> None:
        client = FakeClient(over=2, logs=[])
        beca_logtime.prepare_update_logtime(client, make_update_args(hours="6"))
        check_request = next(
            request
            for request in client.requests
            if request["path"] == "/api/Default/Work_CheckOverInLogtime"
        )
        self.assertEqual(check_request["params"]["oldVal"], "8")

    def test_update_changed_date_uses_zero_as_old_val(self) -> None:
        client = FakeClient(over=2, logs=[])
        beca_logtime.prepare_update_logtime(client, make_update_args(date="2026-07-02", hours="6"))
        check_request = next(
            request
            for request in client.requests
            if request["path"] == "/api/Default/Work_CheckOverInLogtime"
        )
        self.assertEqual(check_request["params"]["oldVal"], "0")

    def test_update_duplicate_detection_excludes_current_logtime(self) -> None:
        client = FakeClient(
            over=2,
            logs=[
                {
                    "Id": 20997,
                    "UserWorkflowId": 1422459,
                    "Ngay": "2026-07-01T00:00:00",
                    "SoGio": 8,
                    "Congviec": "1385175",
                }
            ],
        )
        beca_logtime.prepare_update_logtime(client, make_update_args(hours="6"))

    def test_update_blocks_duplicate_other_row_same_work_and_day(self) -> None:
        client = FakeClient(
            over=2,
            logs=[
                {
                    "Id": 20998,
                    "UserWorkflowId": 1422460,
                    "Ngay": "2026-07-01T00:00:00",
                    "SoGio": 1,
                    "Congviec": "1385175",
                }
            ],
        )
        with self.assertRaisesRegex(beca_logtime.BecaError, "other logtime"):
            beca_logtime.prepare_update_logtime(client, make_update_args(hours="6"))

    def test_update_blocks_non_owner(self) -> None:
        client = FakeClient(over=2, logs=[], user={"email": "other@example.com"})
        with self.assertRaisesRegex(beca_logtime.BecaError, "another user"):
            beca_logtime.prepare_update_logtime(client, make_update_args(hours="6"))

    def test_update_blocks_checkin_row(self) -> None:
        client = FakeClient(over=2, logs=[], existing={"CheckinId": "check-1"})
        with self.assertRaisesRegex(beca_logtime.BecaError, "check-in"):
            beca_logtime.prepare_update_logtime(client, make_update_args(hours="6"))

    def test_resolve_status_by_name(self) -> None:
        client = FakeClient(over=0, logs=[])
        prepared = beca_logtime.prepare_status_update(
            client,
            make_status_args(status="In Progress"),
        )
        self.assertEqual(prepared.target["userWorkflowId"], "1330833")

    def test_status_id_rejects_internal_id(self) -> None:
        client = FakeClient(over=0, logs=[])
        with self.assertRaisesRegex(beca_logtime.BecaError, "userWorkflowId"):
            beca_logtime.prepare_status_update(client, make_status_args(status_id="9213"))

    def test_update_status_uses_status_user_workflow_id(self) -> None:
        client = FakeClient(over=0, logs=[])
        with redirect_stdout(io.StringIO()):
            result = beca_logtime.command_update_status(
                client,
                make_status_args(status="In Progress", yes=True, json=True),
            )
        self.assertEqual(result, 0)
        update_request = next(
            request
            for request in client.requests
            if request["path"] == "/api/Default/Work_UpdateStatusWork"
        )
        self.assertEqual(update_request["params"]["statusId"], "1330833")
        self.assertNotEqual(update_request["params"]["statusId"], "9213")

    def test_cancel_status_blocked_by_default(self) -> None:
        client = FakeClient(over=0, logs=[])
        with self.assertRaisesRegex(beca_logtime.BecaError, "blocked"):
            beca_logtime.prepare_status_update(client, make_status_args(status="Reject"))

    def test_cancel_status_allowed_with_flag(self) -> None:
        client = FakeClient(over=0, logs=[])
        prepared = beca_logtime.prepare_status_update(
            client,
            make_status_args(status="Reject", allow_cancel=True),
        )
        self.assertEqual(prepared.target["userWorkflowId"], "1330839")

    def test_status_preflight_blocks_non_empty_message(self) -> None:
        client = FakeClient(over=0, logs=[], status_guard="Bạn phải hoàn thành tất cả công việc con")
        with self.assertRaisesRegex(beca_logtime.BecaError, "Status validation failed"):
            beca_logtime.prepare_status_update(client, make_status_args(status="Done"))

    def test_progress_normalization(self) -> None:
        self.assertEqual(beca_logtime.normalize_progress_percent("28"), "28%")
        self.assertEqual(beca_logtime.normalize_progress_percent("28%"), "28%")
        self.assertEqual(beca_logtime.normalize_progress_percent("28.5"), "28.5%")
        with self.assertRaisesRegex(beca_logtime.BecaError, "between 0 and 100"):
            beca_logtime.normalize_progress_percent("-1")
        with self.assertRaisesRegex(beca_logtime.BecaError, "between 0 and 100"):
            beca_logtime.normalize_progress_percent("101")

    def test_update_progress_sends_tiendo_put_and_verifies(self) -> None:
        client = FakeClient(over=0, logs=[])
        with redirect_stdout(io.StringIO()):
            result = beca_logtime.command_update_progress(
                client,
                make_progress_args(progress="28.5", yes=True, json=True),
            )
        self.assertEqual(result, 0)
        update_request = next(
            request
            for request in client.requests
            if request["path"] == "/api/Default/Work_UpdateJsonData"
        )
        self.assertEqual(update_request["method"], "PUT")
        self.assertEqual(update_request["params"]["fileName"], "Tiendo")
        self.assertEqual(update_request["params"]["value"], "28.5%")

    def test_update_progress_verify_failure_returns_two(self) -> None:
        client = FakeClient(over=0, logs=[], progress_update_visible=False)
        with redirect_stdout(io.StringIO()):
            result = beca_logtime.command_update_progress(
                client,
                make_progress_args(progress="28", yes=True, json=True),
            )
        self.assertEqual(result, 2)

    def test_daily_parse_entry_with_progress(self) -> None:
        entry = beca_logtime.parse_daily_entry(
            "Phát triển trang Multimedia | 6 | Design lại phần điều khiển trên FE | progress=28.5%"
        )
        self.assertEqual(entry.task_query, "Phát triển trang Multimedia")
        self.assertEqual(entry.hours, "6")
        self.assertEqual(entry.description, "Design lại phần điều khiển trên FE")
        self.assertEqual(entry.progress, "28.5%")

    def test_daily_parse_entry_with_structured_description(self) -> None:
        entry = beca_logtime.parse_daily_entry(
            "Energy | 8 | Xây API | result=Đã chạy | blockers=Không | next=Deploy"
        )
        self.assertEqual(entry.result, "Đã chạy")
        self.assertEqual(entry.blockers, "Không")
        self.assertEqual(entry.next_step, "Deploy")

    def test_daily_total_hours_must_be_exactly_eight(self) -> None:
        client = FakeClient(over=0, logs=[])
        with self.assertRaisesRegex(beca_logtime.BecaError, "exactly 8"):
            beca_logtime.prepare_daily(client, make_daily_args(entry=["Multimedia | 6 | Work", "diagram | 1 | Work"]))
        self.assertEqual(client.calls, [])

    def test_list_logtimes_filters_period_response_to_requested_day(self) -> None:
        client = FakeClient(
            over=0,
            logs=[],
            timesheet_rows=[
                log_row_from_values({"Ngay": "2026-07-01", "SoGio": 8, "Congviec": "1", "Duan": "p"}, 1, 11),
                log_row_from_values({"Ngay": "2026-07-02", "SoGio": 8, "Congviec": "2", "Duan": "p"}, 2, 12),
            ],
        )
        rows = beca_logtime.list_logtimes(
            client,
            Namespace(date="2026-07-01", department=None, project_id="-1"),
        )
        self.assertEqual([row["logId"] for row in rows], ["1"])

    def test_comment_redaction_is_default_and_can_be_disabled(self) -> None:
        secret = "tài khoản test: demo / Secret123 password: Hidden456"
        redacted = beca_logtime.redact_sensitive_text(secret)
        self.assertNotIn("Secret123", redacted)
        self.assertNotIn("Hidden456", redacted)
        self.assertIn("[REDACTED]", redacted)

    def test_api_setting_missing_ids_fails_closed(self) -> None:
        client = FakeClient(over=0, logs=[], api_setting={})
        with self.assertRaisesRegex(beca_logtime.BecaError, "hard-coded"):
            beca_logtime.get_api_setting(client)

    def test_daily_resolves_work_id_exact_substring_and_accent_insensitive(self) -> None:
        client = FakeClient(over=0, logs=[])
        tasks = client.tasks
        self.assertEqual(beca_logtime.resolve_daily_task("1385175", tasks)["userWorkflowId"], "1385175")
        self.assertEqual(
            beca_logtime.resolve_daily_task("Phát triển trang Multimedia", tasks)["userWorkflowId"],
            "1385175",
        )
        self.assertEqual(beca_logtime.resolve_daily_task("diagram flow training", tasks)["userWorkflowId"], "1414089")
        self.assertEqual(
            beca_logtime.resolve_daily_task("Len diagram flow training AI", tasks)["userWorkflowId"],
            "1414089",
        )

    def test_daily_ambiguous_task_match_blocks(self) -> None:
        tasks = [
            {"userWorkflowId": "1", "projectId": "p", "title": "Build API"},
            {"userWorkflowId": "2", "projectId": "p", "title": "Build API"},
        ]
        with self.assertRaisesRegex(beca_logtime.BecaError, "Ambiguous"):
            beca_logtime.resolve_daily_task("Build API", tasks)

    def test_daily_duplicate_blocks_before_any_save(self) -> None:
        client = FakeClient(
            over=0,
            logs=[
                {
                    "Id": 30001,
                    "UserWorkflowId": 40001,
                    "Ngay": "2026-07-01T00:00:00",
                    "SoGio": 6,
                    "Congviec": "1385175",
                }
            ],
        )
        with self.assertRaisesRegex(beca_logtime.BecaError, "existing logtime"):
            beca_logtime.command_daily(
                client,
                make_daily_args(entry=["Multimedia | 6 | Work", "diagram | 2 | Work"], yes=True),
            )
        self.assertFalse(any("Eoffice_ValidateAndInsertData" in call for call in client.calls))

    def test_daily_preview_defaults_to_human_readable_text(self) -> None:
        client = FakeClient(over=0, logs=[])
        prepared = beca_logtime.prepare_daily(
            client,
            make_daily_args(entry=["Multimedia | 6 | Design FE | progress=28", "diagram | 2 | Vẽ pipeline"]),
        )
        text = beca_logtime.render_daily_preview_text(beca_logtime.daily_preview_dict(prepared))
        self.assertIn("Preview daily logtime", text)
        self.assertIn("Tổng giờ: 8", text)
        self.assertIn("Phát triển trang Multimedia", text)
        self.assertIn("Progress sau log: 28%", text)
        self.assertFalse(text.lstrip().startswith("{"))

    def test_daily_submit_order_and_progress_after_verify(self) -> None:
        client = FakeClient(over=0, logs=[])
        with redirect_stdout(io.StringIO()):
            result = beca_logtime.command_daily(
                client,
                make_daily_args(
                    entry=[
                        "Multimedia | 6 | Design FE | progress=28",
                        "diagram | 2 | Vẽ pipeline",
                    ],
                    yes=True,
                    json=True,
                ),
            )
        self.assertEqual(result, 0)
        task_index = client.calls.index("GET:/api/Default/Work_GetWorkInProcess")
        first_prepare_index = client.calls.index("GET:/api/Default/Work_GetApiSetting")
        self.assertLess(task_index, first_prepare_index)
        first_validate_index = client.calls.index("GET:/api/Default/Work_CheckOverInLogtime")
        first_save_index = next(
            index
            for index, call in enumerate(client.calls)
            if call.startswith("POST:/api/ApiEoffice/Eoffice_ValidateAndInsertData")
        )
        self.assertLess(first_validate_index, first_save_index)
        first_verify_after_save = next(
            index
            for index, call in enumerate(client.calls)
            if index > first_save_index and call == "GET:/api/Default/Work_GetLogtimeByWorkId"
        )
        progress_update_index = next(index for index, call in enumerate(client.calls) if call == "PUT:/api/Default/Work_UpdateJsonData")
        self.assertLess(first_verify_after_save, progress_update_index)

    def test_daily_verify_failure_reports_partial_result(self) -> None:
        client = FakeClient(over=0, logs=[], save_visible_sequence=[True, False])
        output = io.StringIO()
        with redirect_stdout(output):
            result = beca_logtime.command_daily(
                client,
                make_daily_args(
                    entry=[
                        "Multimedia | 6 | Design FE",
                        "diagram | 2 | Vẽ pipeline",
                    ],
                    yes=True,
                    json=True,
                ),
            )
        self.assertEqual(result, 2)
        last = last_json_object(output.getvalue())
        self.assertTrue(last["saved"])
        self.assertFalse(last["complete"])
        self.assertTrue(last["entries"][0]["logtime"]["verified"])
        self.assertFalse(last["entries"][1]["logtime"]["verified"])
        self.assertIn("failedEntry", last)


def make_args(**overrides):
    values = {
        "project_id": "1330830",
        "work_id": "1415256",
        "date": "2026-07-01",
        "hours": "8",
        "action": "Thực hiện",
        "description": "Worked on Public Wifi backend",
        "allow_duplicate": False,
        "yes": False,
        "json": False,
    }
    values.update(overrides)
    return Namespace(**values)


def make_update_args(**overrides):
    values = {
        "log_id": "20997",
        "date": None,
        "hours": None,
        "action": None,
        "description": None,
        "allow_duplicate": False,
        "yes": False,
        "json": False,
    }
    values.update(overrides)
    return Namespace(**values)


def make_status_args(**overrides):
    values = {
        "work_id": "1415256",
        "status": None,
        "status_id": None,
        "allow_cancel": False,
        "yes": False,
        "json": False,
    }
    values.update(overrides)
    return Namespace(**values)


def make_progress_args(**overrides):
    values = {
        "work_id": "1415256",
        "progress": "28",
        "yes": False,
        "json": False,
    }
    values.update(overrides)
    return Namespace(**values)


def make_daily_args(**overrides):
    values = {
        "entry": ["Multimedia | 6 | Design FE", "diagram | 2 | Vẽ pipeline"],
        "date": "2026-07-01",
        "allow_duplicate": False,
        "yes": False,
        "json": False,
    }
    values.update(overrides)
    return Namespace(**values)


def make_existing(**overrides):
    values = {
        "Title": "Phát triển trang Multimedia",
        "DateLogtime": "2026-07-01T00:00:00",
        "UserWorkflowId": 1422459,
        "Mota": "<p>Old work</p>",
        "Id": 20997,
        "Nguoilap": "Demo User",
        "Ngaylap": "2026-07-01T08:23:17",
        "Hanhdong": "Thực hiện",
        "Congviec": "1385175",
        "Ngay": "2026-07-01T00:00:00",
        "Email": "user@example.com",
        "SoGio": 8,
        "Duan": "1330830",
        "CheckinId": None,
        "UserId": "P:10000",
    }
    values.update(overrides)
    return values


def json_from_payload(payload):
    return beca_logtime.json.loads(payload["data_json"])


def json_objects(output):
    decoder = beca_logtime.json.JSONDecoder()
    index = 0
    items = []
    while index < len(output):
        while index < len(output) and output[index].isspace():
            index += 1
        if index >= len(output):
            break
        item, index = decoder.raw_decode(output, index)
        items.append(item)
    return items


def last_json_object(output):
    return json_objects(output)[-1]


def log_row_from_values(values, log_id, log_user_workflow_id, title="Saved task"):
    return {
        "Id": log_id,
        "UserWorkflowId": log_user_workflow_id,
        "Congviec": values.get("Congviec"),
        "Duan": values.get("Duan"),
        "Ngay": values.get("Ngay"),
        "SoGio": values.get("SoGio"),
        "Mota": values.get("Mota"),
        "Title": title,
        "Email": values.get("Email"),
        "UserId": values.get("UserId"),
    }


class FakeClient:
    def __init__(
        self,
        over,
        logs,
        existing=None,
        user=None,
        save_visible=True,
        save_visible_sequence=None,
        next_log_id=20998,
        next_user_workflow_id=1429999,
        updated_log_id=21041,
        tasks=None,
        task_detail=None,
        next_statuses=None,
        rule_guard="None",
        form_guard="",
        status_guard="",
        progress_update_visible=True,
        timesheet_rows=None,
        api_setting=None,
    ) -> None:
        self.over = over
        self.logs = logs
        self.existing = make_existing(**(existing or {}))
        self.user = {
            "id": "P:10000",
            "fullName": "Demo User",
            "email": "user@example.com",
            "departmentId": 5,
            **(user or {}),
        }
        self.save_visible = save_visible
        self.save_visible_sequence = list(save_visible_sequence) if save_visible_sequence is not None else None
        self.next_log_id = next_log_id
        self.next_user_workflow_id = next_user_workflow_id
        self.updated_log_id = updated_log_id
        self.tasks = tasks or [
            {
                "projectId": "1330830",
                "projectName": "R&D-SmartPole-Pilot-2026",
                "userWorkflowId": "1385175",
                "title": "Phát triển trang Multimedia",
                "statusName": "In Progress",
                "isMyWork": True,
            },
            {
                "projectId": "1330830",
                "projectName": "R&D-SmartPole-Pilot-2026",
                "userWorkflowId": "1414089",
                "title": "Lên diagram flow training AI và demo cho 1 bài toán thực tế",
                "statusName": "Open",
                "isMyWork": True,
            },
            {
                "projectId": "1330830",
                "projectName": "R&D-SmartPole-Pilot-2026",
                "userWorkflowId": "1415256",
                "title": "Phát triển hệ thống Wifi công cộng (Public Wifi)",
                "statusName": "Open",
                "isMyWork": True,
            },
        ]
        self.task_detail = {
            "userWorkflowId": "1415256",
            "title": "Phát triển hệ thống Wifi công cộng (Public Wifi)",
            "projectId": "1330830",
            "projectName": "R&D-SmartPole-Pilot-2026",
            "status": "1330831",
            "statusName": "Open",
            "statusType": "Cơ bản",
            "progress": "20%",
            "createdBy": "user@example.com",
            "userExecStatus": "",
            "dateExec": "",
            **(task_detail or {}),
        }
        self.next_statuses = next_statuses or [
            {
                "id": 9212,
                "name": "Open",
                "projectId": "1330830",
                "trangThaiCongViec": "Cơ bản",
                "userWorkflowId": "1330831",
                "isCurrentStatus": True,
            },
            {
                "id": 9213,
                "name": "In Progress",
                "projectId": "1330830",
                "trangThaiCongViec": "Cơ bản",
                "userWorkflowId": "1330833",
                "isCurrentStatus": False,
            },
            {
                "id": 9215,
                "name": "Need to Test",
                "projectId": "1330830",
                "trangThaiCongViec": "Người xử lý hoàn thành",
                "userWorkflowId": "1330836",
                "isCurrentStatus": False,
            },
            {
                "id": 9222,
                "name": "Done",
                "projectId": "1330830",
                "trangThaiCongViec": "Hoàn thành",
                "userWorkflowId": "1330837",
                "isCurrentStatus": False,
            },
            {
                "id": 9219,
                "name": "Reject",
                "projectId": "1330830",
                "trangThaiCongViec": "Hủy",
                "userWorkflowId": "1330839",
                "isCurrentStatus": False,
            },
        ]
        self.rule_guard = rule_guard
        self.form_guard = form_guard
        self.status_guard = status_guard
        self.progress_update_visible = progress_update_visible
        self.timesheet_rows = list(timesheet_rows or [])
        self.api_setting = api_setting if api_setting is not None else {
            "id_getWorkFormLogTime": {"id": 101},
            "id_getWorkFormLogTimeStep": {"id": 415},
        }
        self.calls = []
        self.requests = []

    def get_json(self, path, params=None):
        self.calls.append(f"GET:{path}")
        self.requests.append({"method": "GET", "path": path, "params": params or {}})
        if path == "/api/Default/Work_GetApiSetting":
            return self.api_setting
        if path == "/api/Default/Work_GetInfLogin":
            return self.user
        if path == "/api/Default/Work_CheckOverInLogtime":
            return self.over
        if path == "/api/Default/Work_GetLogtimeByWorkId":
            return self.logs
        if path == "/api/Default/Work_GetLogtimeById":
            return self.existing
        if path == "/api/Default/Work_GetWorkInProcess":
            return self.tasks
        if path == "/api/Default/Work_TimeSheetPersonalLayoutList":
            return self.timesheet_rows
        if path == "/api/Default/Work_DetailInfo":
            return self.task_detail
        if path == "/api/Default/Work_GetNextStatus":
            return self.next_statuses
        if path == "/api/Default/Work_CheckRuleUpdateProcessWork":
            return self.rule_guard
        if path == "/api/Default/Work_CheckBeforeSaveChangStatusWithFormExtendInfo":
            return self.form_guard
        if path == "/api/Default/Work_CheckBeforeSaveChangStatus":
            return self.status_guard
        if path == "/api/Default/Work_UpdateStatusWork":
            target = next(
                (
                    status
                    for status in self.next_statuses
                    if str(status.get("userWorkflowId")) == str((params or {}).get("statusId"))
                ),
                None,
            )
            if target:
                self.task_detail["status"] = str(target["userWorkflowId"])
                self.task_detail["statusName"] = target["name"]
                self.task_detail["statusType"] = target["trangThaiCongViec"]
            return {"ok": True}
        raise AssertionError(path)

    def post_json(self, path, payload, params=None, extra_headers=None):
        self.calls.append(f"POST:{path}")
        self.requests.append({"method": "POST", "path": path, "params": params or {}, "payload": payload})
        if path == "/api/ApiEoffice/Eoffice_GetData":
            return [
                {
                    "row": [
                        {"name": "Nguoilap", "defaultValue": "Demo User"},
                        {"name": "Email", "defaultValue": "user@example.com"},
                        {
                            "name": "SoGio",
                            "type": "number",
                            "minimum": 1.0,
                            "maximum": 16.0,
                            "numberOfDecimalPlaces": 0,
                        },
                        {
                            "name": "Hanhdong",
                            "type": "select",
                            "selectItems": [
                                {"label": "Thực hiện", "value": "Thực hiện"},
                                {"label": "Xem xét", "value": "Xem xét"},
                                {"label": "Kiểm thử", "value": "Kiểm thử"},
                                {"label": "Theo dõi", "value": "Theo dõi"},
                            ],
                        },
                        {
                            "name": "Mota",
                            "type": "editor",
                            "defaultValue": (
                                "<ul><li><p><em>Đã thực hiện</em>: </p></li>"
                                "<li><p><em>Kết quả</em>:</p></li>"
                                "<li><p><em>Vướng mắc</em>:</p></li>"
                                "<li><p><em>Bước tiếp theo</em>:</p></li></ul>"
                            ),
                        },
                    ]
                }
            ]
        if path.startswith("/api/ApiEoffice/Eoffice_ValidateAndInsertData"):
            save_visible = self.save_visible
            if self.save_visible_sequence is not None and self.save_visible_sequence:
                save_visible = self.save_visible_sequence.pop(0)
            if save_visible:
                values = beca_logtime.values_from_payload(payload)
                self.logs.append(
                    log_row_from_values(
                        values,
                        self.next_log_id,
                        self.next_user_workflow_id,
                    )
                )
                self.next_log_id += 1
                self.next_user_workflow_id += 1
            return {
                "id": self.next_user_workflow_id,
                "workflowCode": "VNTTSLTTEST",
                "status": "Phê duyệt",
                "workflowTitle": "[VNTTS]_LogTime",
            }
        raise AssertionError(path)

    def put_json(self, path, payload, params=None, extra_headers=None):
        self.calls.append(f"PUT:{path}")
        self.requests.append({"method": "PUT", "path": path, "params": params or {}, "payload": payload})
        if path.startswith("/api/ApiEoffice/Eoffice_UpdateData"):
            if self.save_visible:
                values = beca_logtime.values_from_payload(payload)
                current_user_workflow_id = self.existing["UserWorkflowId"]
                self.logs = [
                    row
                    for row in self.logs
                    if str(row.get("UserWorkflowId") or row.get("userWorkflowId") or "") != str(current_user_workflow_id)
                ]
                self.logs.append(
                    log_row_from_values(
                        values,
                        self.updated_log_id,
                        current_user_workflow_id,
                        title=self.existing.get("Title", "Updated task"),
                    )
                )
            return {"ok": True}
        if path == "/api/Default/Work_UpdateJsonData":
            if self.progress_update_visible and (params or {}).get("fileName") == "Tiendo":
                self.task_detail["progress"] = (params or {}).get("value")
            return {"ok": True}
        raise AssertionError(path)


if __name__ == "__main__":
    unittest.main()
