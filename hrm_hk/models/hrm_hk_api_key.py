import hashlib
import secrets

from odoo import api, fields, models, _
from odoo.exceptions import UserError


class HrmHkApiKey(models.Model):
    """Server-to-server credential for the employee web app backend.

    Employee (browser) calls use short-lived JWTs issued by /api/hrm/v1/auth/login.
    This model is for the Next.js *server* to make elevated/admin calls
    (e.g. HR sync jobs). Keys are stored hashed; the raw value is shown once.
    """
    _name = 'hrm.hk.api.key'
    _description = 'HRM API Key'

    name = fields.Char(required=True)
    user_id = fields.Many2one(
        'res.users', string='Acts As', required=True,
        help='Requests authenticated with this key run with this user\'s rights.',
    )
    key_hash = fields.Char(readonly=True)
    key_preview = fields.Char(string='Key', readonly=True)
    scope = fields.Char(default='read', help='Comma-separated scopes, e.g. read,write.')
    active = fields.Boolean(default=True)

    def action_generate_key(self):
        self.ensure_one()
        raw = secrets.token_urlsafe(32)
        self.write({
            'key_hash': hashlib.sha256(raw.encode()).hexdigest(),
            'key_preview': f'{raw[:6]}…{raw[-4:]}',
        })
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('API key generated'),
                'message': _('Copy it now, it will not be shown again:\n%s', raw),
                'sticky': True,
                'type': 'warning',
            },
        }

    @api.model
    def _verify(self, raw_key):
        if not raw_key:
            return self.browse()
        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
        return self.sudo().search(
            [('key_hash', '=', key_hash), ('active', '=', True)], limit=1)
