"""Gmail transport and multipart report messages; no external packages."""
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import format_datetime
import hashlib
import json
import re
import smtplib
import ssl


class MailError(Exception):
    def __init__(self, message, uncertain=False):
        super().__init__(message)
        self.uncertain = uncertain


def validate_settings(config):
    try:
        if type(config['enabled']) is not bool:
            raise ValueError
        if not config['enabled']:
            return dict(enabled=False)
        username = config['username'].strip()
        password = config['app_password'].replace(' ', '').strip()
        recipients = config['recipients']
        if isinstance(recipients, str):
            recipients = [r.strip() for r in re.split('[,;\n]+', recipients) if r.strip()]
        if not isinstance(recipients, list) or not recipients or not password:
            raise ValueError
        recipients = list(dict.fromkeys(r.strip() for r in recipients))
        for address in [username, *recipients]:
            if not address.isascii() or not re.fullmatch(r'[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]+', address):
                raise ValueError
        return dict(enabled=True, username=username, app_password=password, recipients=recipients)
    except (ValueError, TypeError, KeyError, AttributeError):
        raise MailError('Gmail設定が不正です。送信元・アプリパスワード・宛先を確認してください。') from None


def load_settings(root):
    path = root / '.local/mail.json'
    if not path.exists():
        return None
    try:
        config = validate_settings(json.loads(path.read_text(encoding='utf-8')))
    except (OSError, ValueError):
        raise MailError('メール設定を読み取れません。setup-mailで設定し直してください。') from None
    return config if config['enabled'] else None


def make_message(sender, recipient, scheduled_at, text, report):
    slot = datetime.fromisoformat(scheduled_at)
    message = EmailMessage()
    message['Subject'] = f'Twitchゲーム視聴者数・前日比較 {slot:%Y-%m-%d %H:%M} JST'
    message['From'] = sender
    message['To'] = recipient
    message['Date'] = format_datetime(datetime.now(timezone.utc))
    digest = hashlib.sha256(f'{scheduled_at}\0{recipient}'.encode()).hexdigest()
    message['Message-ID'] = f'<{digest}@research-viewer.local>'
    message.set_content(text)
    message.add_alternative(report, subtype='html')
    message.add_attachment(report, subtype='html', filename=f'twitch-{slot:%Y-%m-%d-%H}.html')
    return message


def send(config, message):
    server = None
    sending = False
    try:
        server = smtplib.SMTP_SSL('smtp.gmail.com', 465, timeout=30, context=ssl.create_default_context())
        server.login(config['username'], config['app_password'])
        sending = True
        refused = server.send_message(message, from_addr=config['username'], to_addrs=[str(message['To'])])
        if refused:
            raise MailError('宛先がSMTPサーバーに拒否されました。')
    except (smtplib.SMTPRecipientsRefused, smtplib.SMTPSenderRefused, smtplib.SMTPDataError):
        raise MailError('SMTPサーバーがメールを拒否しました。設定と送信制限を確認してください。') from None
    except (OSError, ValueError):
        raise MailError('Gmailへの送信中に接続または認証のエラーが発生しました。', uncertain=sending) from None
    finally:
        if server is not None:
            try:
                server.quit()
            except OSError:
                pass
            finally:
                try:
                    server.close()
                except OSError:
                    pass
