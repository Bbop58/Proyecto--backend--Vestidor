"""Small transactional message adapter sharing the existing Brevo configuration."""
import json
from urllib.parse import urlsplit
from urllib.request import Request,urlopen
from app.config import settings
from app.services.receipt_email_service import email_enabled,_address

def mail_available():
    return settings.RECEIPT_EMAIL_PROVIDER=='brevo' and email_enabled()

def invitation_url(token):
    base=urlsplit(settings.SUBSCRIPTION_WEB_URL)
    if base.scheme not in ('https','http') or not base.netloc or base.username or base.password:
        raise ValueError('Invalid application URL')
    return f'{base.scheme}://{base.netloc}/auth/invitacion-empresa#{token}'

def send_invitation(email,company,url):
    payload={'sender':{'email':_address(settings.SMTP_FROM),'name':'FashionStore'},
        'to':[{'email':_address(email)}],'subject':'Invitación para administrar tu empresa · FashionStore',
        'textContent':f'Te invitaron a administrar {company}.\n\nVerifica tu correo y crea tu contraseña desde este enlace privado:\n{url}\n\nVence en 48 horas y solo puede usarse una vez. Luego podrás elegir y contratar un plan. No se ha registrado ningún cobro. Si no esperabas esta invitación, ignora este correo.'}
    request=Request('https://api.brevo.com/v3/smtp/email',data=json.dumps(payload).encode(),
        headers={'api-key':settings.BREVO_API_KEY.get_secret_value(),'Content-Type':'application/json','Accept':'application/json'})
    with urlopen(request,timeout=25) as response:
        if response.status!=201 or not json.loads(response.read()).get('messageId'):
            raise RuntimeError('Mail acceptance uncertain')
