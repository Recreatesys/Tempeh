import base64
import hashlib
import hmac
import json
import time

from odoo import http
from odoo.http import request
from odoo.exceptions import AccessDenied

ACCESS_TTL = 60 * 60           # 1 hour
REFRESH_TTL = 60 * 60 * 24 * 30  # 30 days
PARAM_SECRET = 'hrm_hk.jwt_secret'


# --- minimal JWT (HS256) so we carry no external dependency -----------------

def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b'=').decode()


def _b64url_decode(seg: str) -> bytes:
    return base64.urlsafe_b64decode(seg + '=' * (-len(seg) % 4))


def _secret(env) -> bytes:
    val = env['ir.config_parameter'].sudo().get_param(PARAM_SECRET)
    if not val:
        import secrets
        val = secrets.token_urlsafe(48)
        env['ir.config_parameter'].sudo().set_param(PARAM_SECRET, val)
    return val.encode()


def jwt_encode(env, payload: dict) -> str:
    header = {'alg': 'HS256', 'typ': 'JWT'}
    segments = [
        _b64url(json.dumps(header, separators=(',', ':')).encode()),
        _b64url(json.dumps(payload, separators=(',', ':')).encode()),
    ]
    signing_input = '.'.join(segments).encode()
    sig = hmac.new(_secret(env), signing_input, hashlib.sha256).digest()
    segments.append(_b64url(sig))
    return '.'.join(segments)


def jwt_decode(env, token: str) -> dict:
    """Return the payload if the signature and exp are valid, else None."""
    try:
        h_seg, p_seg, s_seg = token.split('.')
        signing_input = f'{h_seg}.{p_seg}'.encode()
        expected = hmac.new(_secret(env), signing_input, hashlib.sha256).digest()
        if not hmac.compare_digest(expected, _b64url_decode(s_seg)):
            return None
        payload = json.loads(_b64url_decode(p_seg))
    except Exception:
        return None
    if payload.get('exp', 0) < int(time.time()):
        return None
    return payload


def _json(data, status=200):
    return request.make_json_response(data, status=status)


class HrmHkAuthController(http.Controller):

    @http.route('/api/hrm/v1/auth/login', type='http', auth='none',
                methods=['POST'], csrf=False, save_session=False)
    def login(self, **kw):
        try:
            body = json.loads(request.httprequest.data or b'{}')
        except ValueError:
            return _json({'error': 'invalid_json'}, 400)
        login = body.get('login')
        password = body.get('password')
        if not login or not password:
            return _json({'error': 'missing_credentials'}, 400)
        credential = {'login': login, 'password': password, 'type': 'password'}
        try:
            # Odoo 18: authenticate(dbname, credential) — a db name string,
            # not an env (that signature is Odoo 19-only).
            auth_info = request.session.authenticate(request.db, credential)
        except AccessDenied:
            return _json({'error': 'invalid_credentials'}, 401)
        uid = auth_info['uid'] if isinstance(auth_info, dict) else auth_info
        employee = request.env['hr.employee'].sudo().search(
            [('user_id', '=', uid)], limit=1)
        now = int(time.time())
        access = jwt_encode(request.env, {
            'uid': uid, 'eid': employee.id, 'typ': 'access', 'exp': now + ACCESS_TTL})
        refresh = jwt_encode(request.env, {
            'uid': uid, 'typ': 'refresh', 'exp': now + REFRESH_TTL})
        return _json({
            'access_token': access,
            'refresh_token': refresh,
            'token_type': 'Bearer',
            'expires_in': ACCESS_TTL,
            'employee': employee._hrm_hk_self_data() if employee else None,
        })

    @http.route('/api/hrm/v1/auth/refresh', type='http', auth='none',
                methods=['POST'], csrf=False, save_session=False)
    def refresh(self, **kw):
        try:
            body = json.loads(request.httprequest.data or b'{}')
        except ValueError:
            return _json({'error': 'invalid_json'}, 400)
        payload = jwt_decode(request.env, body.get('refresh_token', ''))
        if not payload or payload.get('typ') != 'refresh':
            return _json({'error': 'invalid_refresh_token'}, 401)
        uid = payload['uid']
        employee = request.env['hr.employee'].sudo().search(
            [('user_id', '=', uid)], limit=1)
        now = int(time.time())
        access = jwt_encode(request.env, {
            'uid': uid, 'eid': employee.id, 'typ': 'access', 'exp': now + ACCESS_TTL})
        return _json({'access_token': access, 'token_type': 'Bearer',
                      'expires_in': ACCESS_TTL})
