from odoo import fields, models


class HrmHkPushSubscription(models.Model):
    _name = 'hrm.hk.push.subscription'
    _description = 'Web Push Subscription'

    employee_id = fields.Many2one(
        'hr.employee', string='Employee', required=True, ondelete='cascade', index=True)
    endpoint = fields.Char(required=True, index=True)
    p256dh = fields.Char(required=True)
    auth = fields.Char(required=True)
    ua = fields.Char(string='User Agent')
    active = fields.Boolean(default=True)

    _sql_constraints = [
        ('endpoint_uniq', 'unique(endpoint)', 'This push endpoint is already registered.'),
    ]

    def _upsert(self, employee, endpoint, p256dh, auth, ua=None):
        """Create or refresh a subscription keyed by its endpoint."""
        existing = self.sudo().search([('endpoint', '=', endpoint)], limit=1)
        vals = {'employee_id': employee.id, 'p256dh': p256dh, 'auth': auth,
                'ua': ua, 'active': True}
        if existing:
            existing.write(vals)
            return existing
        return self.sudo().create(dict(vals, endpoint=endpoint))
