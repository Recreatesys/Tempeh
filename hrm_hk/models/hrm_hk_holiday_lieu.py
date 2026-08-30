from datetime import timedelta

from odoo import api, fields, models, _
from odoo.exceptions import UserError


class HrmHkHolidayLieu(models.Model):
    _name = 'hrm.hk.holiday.lieu'
    _description = 'Statutory Holiday Worked - Day Off in Lieu'
    _order = 'holiday_date desc, id desc'

    name = fields.Char(compute='_compute_name', store=True)
    employee_id = fields.Many2one(
        'hr.employee', string='Employee', required=True, ondelete='cascade', index=True)
    holiday_date = fields.Date(string='Statutory Holiday Worked', required=True)
    holiday_name = fields.Char(string='Holiday')
    days = fields.Float(string='Days in Lieu', default=1.0)
    # EO: an alternative holiday should be within 60 days of the statutory holiday
    expiry_date = fields.Date(string='Take By', compute='_compute_expiry', store=True)
    state = fields.Selection([
        ('draft', 'To Grant'),
        ('granted', 'Granted'),
        ('cancelled', 'Cancelled'),
    ], default='draft', required=True, tracking=True)
    allocation_id = fields.Many2one('hr.leave.allocation', readonly=True, copy=False)
    note = fields.Text()
    company_id = fields.Many2one('res.company', default=lambda self: self.env.company)

    _sql_constraints = [
        ('uniq_emp_date', 'unique(employee_id, holiday_date)',
         'A day off in lieu is already recorded for this employee and holiday.'),
    ]

    @api.depends('employee_id', 'holiday_name', 'holiday_date')
    def _compute_name(self):
        for rec in self:
            rec.name = '%s - %s' % (
                rec.employee_id.name or '?', rec.holiday_name or rec.holiday_date or '')

    @api.depends('holiday_date')
    def _compute_expiry(self):
        for rec in self:
            rec.expiry_date = rec.holiday_date + timedelta(days=60) if rec.holiday_date else False

    def action_grant(self):
        """Create a validated Holiday-in-Lieu allocation and notify the employee."""
        lieu_type = self.env.ref('hrm_hk.leave_type_hk_lieu')
        for rec in self:
            if rec.state == 'granted':
                continue
            alloc = self.env['hr.leave.allocation'].sudo().create({
                'name': _('Day off in lieu: %s', rec.holiday_name or rec.holiday_date),
                'holiday_status_id': lieu_type.id,
                'employee_id': rec.employee_id.id,
                'number_of_days': rec.days,
                'allocation_type': 'regular',
            })
            for method in ('action_validate', 'action_approve'):
                if hasattr(alloc, method):
                    try:
                        getattr(alloc, method)()
                        break
                    except Exception:
                        pass
            rec.allocation_id = alloc.id
            rec.state = 'granted'
            self.env['hrm.hk.notification'].sudo()._notify(
                rec.employee_id, 'info',
                name=_('You earned a day off in lieu 補假'),
                body=_('For working on %s. You can request it as leave anytime before %s.',
                       rec.holiday_name or rec.holiday_date, rec.expiry_date),
                about=rec.employee_id, res_model='hrm.hk.holiday.lieu', res_id=rec.id)
        return True

    def action_cancel(self):
        for rec in self:
            if rec.allocation_id and rec.allocation_id.state != 'refuse':
                try:
                    rec.allocation_id.action_refuse()
                except Exception:
                    pass
            rec.state = 'cancelled'

    @api.ondelete(at_uninstall=False)
    def _unlink_only_draft(self):
        if any(r.state == 'granted' for r in self):
            raise UserError(_('Cancel a granted lieu record before deleting it.'))
