"""SES v2 login-code mail: one plain-text zh-TW message per code.

``SesMailer`` implements the application's :class:`MailerPort` over the SES v2
``send_email`` API. It takes a ``client_factory`` so no test - and no import of
this module - ever touches the network or boto3; ``sesv2_client_factory`` is the
provided lazy-import factory for the composition root.

Failures are mapped to the typed, sanitized
:class:`~appraisal_review.application.email_login.MailDeliveryUnavailable`:

- ``delivery_restricted``: the account or recipient cannot be delivered to right
  now. In an SES SANDBOX account (the current state: production access not
  requested) ``MessageRejected`` is the honest signal that the recipient is not
  a verified identity - the only deliverable mailbox today is the verified test
  address. Also covers a paused or suspended sending account and an unverified
  From domain (no DNS/domain identity is configured).
- ``throttled``: the account send-rate or daily quota refused the message
  (sandbox: 200 messages/day, 1 message/second).
- ``provider_unavailable``: anything else; the raw provider message never
  crosses this boundary.

Rate pacing (max 1 message/second in sandbox) is the caller's concern: the login
service's own per-email and per-caller limits keep request-code volume far below
it, and this adapter sends exactly one message per call.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from appraisal_review.application.email_login import MailDeliveryUnavailable

_SUBJECT = "估價審查系統登入驗證碼"

_RESTRICTED_CODES = frozenset(
    {
        "MessageRejected",
        "MailFromDomainNotVerifiedException",
        "MailFromDomainNotVerified",
        "SendingPausedException",
        "AccountSuspendedException",
    }
)
_THROTTLED_CODES = frozenset(
    {"TooManyRequestsException", "LimitExceededException", "ThrottlingException", "Throttling"}
)


def sesv2_client_factory(region: str) -> Callable[[], Any]:
    """Lazy boto3 factory for the composition root; importing this module is free."""

    def factory() -> Any:
        import boto3

        return boto3.client("sesv2", region_name=region)

    return factory


def _mail_body(code: str) -> str:
    return (
        "您好:\n\n"
        f"您的登入驗證碼為:{code}\n\n"
        "此驗證碼自寄出起 10 分鐘內有效,且僅能使用一次。\n"
        "請勿將驗證碼提供給任何人。\n"
        "若您並未申請登入,請忽略此郵件,您的帳戶不會有任何變動。\n\n"
        "新北市不動產估價審查系統"
    )


def _failure_reason(error: Exception) -> str:
    response = getattr(error, "response", None)
    code = ""
    if isinstance(response, dict):
        detail = response.get("Error")
        if isinstance(detail, dict):
            code = str(detail.get("Code", ""))
    if code in _RESTRICTED_CODES:
        return "delivery_restricted"
    if code in _THROTTLED_CODES:
        return "throttled"
    return "provider_unavailable"


class SesMailer:
    """MailerPort over SES v2 send_email; the From address is configuration."""

    def __init__(self, client_factory: Callable[[], Any], *, sender: str) -> None:
        if not sender or "@" not in sender:
            raise ValueError("A configured From mailbox address is required")
        self._client_factory = client_factory
        self._client: Any = None
        self.sender = sender

    def send_login_code(self, email: str, code: str) -> None:
        if self._client is None:
            self._client = self._client_factory()
        try:
            self._client.send_email(
                FromEmailAddress=self.sender,
                Destination={"ToAddresses": [email]},
                Content={
                    "Simple": {
                        "Subject": {"Data": _SUBJECT, "Charset": "UTF-8"},
                        "Body": {"Text": {"Data": _mail_body(code), "Charset": "UTF-8"}},
                    }
                },
            )
        except Exception as error:
            # Sanitized and typed; the login service records it for operators only.
            raise MailDeliveryUnavailable(_failure_reason(error)) from error
