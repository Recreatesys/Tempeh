from odoo import fields, models


class HrmHkNotice(models.Model):
    _name = 'hrm.hk.notice'
    _description = 'Employee Notice / Announcement'
    _order = 'pinned desc, publish_date desc, id desc'

    name = fields.Char(string='Title', required=True, translate=True)
    body = fields.Text(string='Content', translate=True)
    category = fields.Selection([
        ('general', 'General'),
        ('holiday', 'Holiday'),
        ('safety', 'Site Safety'),
        ('payroll', 'Payroll'),
        ('event', 'Event'),
    ], default='general', required=True)
    publish_date = fields.Date(string='Publish Date', default=fields.Date.context_today)
    pinned = fields.Boolean(string='Pin to top')
    active = fields.Boolean(default=True)
    company_id = fields.Many2one('res.company', default=lambda self: self.env.company)

    def _notice_payload(self):
        return [{
            'id': n.id,
            'title': n.name,
            'body': n.body,
            'category': n.category,
            'date': str(n.publish_date) if n.publish_date else None,
            'pinned': n.pinned,
        } for n in self]
