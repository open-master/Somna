"""通过 SMTP 发送纯文本邮件（在线程池中同步 smtplib，兼容 163 等）。"""

from __future__ import annotations

import asyncio
import smtplib
from email.message import EmailMessage

from app.config import get_settings


def _send_sync(*, to: str, subject: str, body: str) -> None:
    s = get_settings()
    if not s.smtp_host or not s.smtp_user:
        raise RuntimeError("SMTP not configured")
    if not s.smtp_password:
        raise RuntimeError("SMTP password empty")
    from_addr = (s.smtp_from or s.smtp_user).strip()
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = to
    msg.set_content(body)
    host = s.smtp_host.strip()
    port = int(s.smtp_port)
    if port == 465:
        with smtplib.SMTP_SSL(host, port, timeout=30) as smtp:
            smtp.login(s.smtp_user, s.smtp_password)
            smtp.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=30) as smtp:
            if s.smtp_use_tls:
                smtp.starttls()
            smtp.login(s.smtp_user, s.smtp_password)
            smtp.send_message(msg)


async def send_plain_email(*, to: str, subject: str, body: str) -> None:
    await asyncio.to_thread(_send_sync, to=to, subject=subject, body=body)
