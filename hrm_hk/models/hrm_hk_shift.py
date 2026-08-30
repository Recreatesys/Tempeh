from datetime import timedelta

from odoo import api, fields, models, _
from odoo.exceptions import ValidationError

MAX_CONSECUTIVE_WORK_DAYS = 6


class HrmHkShift(models.Model):
    _name = 'hrm.hk.shift'
    _description = 'Worker Site Shift'
    _order = 'start_datetime desc'

    name = fields.Char(compute='_compute_name', store=True)
    employee_id = fields.Many2one(
        'hr.employee', string='Worker', required=True, ondelete='cascade', index=True)
    work_location_id = fields.Many2one('hr.work.location', string='Site')
    shift_type_id = fields.Many2one(
        'hr.work.entry.type', string='Shift', domain=[('hk_shift', '=', True)],
        help='A / B / C shift — determines the working hours and pay rate.')
    pay_multiplier = fields.Float(
        related='shift_type_id.hk_pay_multiplier', string='Pay ×')
    start_datetime = fields.Datetime(string='Start', required=True)
    end_datetime = fields.Datetime(string='End', required=True)
    planned_hours = fields.Float(
        string='Hours', compute='_compute_planned_hours', store=True)
    state = fields.Selection(
        [('draft', 'Draft'), ('published', 'Published'), ('confirmed', 'Confirmed')],
        default='draft', index=True, tracking=True)
    note = fields.Text()
    color = fields.Integer(related='work_location_id.id', string='Color')
    company_id = fields.Many2one(
        'res.company', default=lambda self: self.env.company)

    @api.depends('employee_id', 'work_location_id')
    def _compute_name(self):
        for shift in self:
            shift.name = '%s @ %s' % (
                shift.employee_id.name or '?', shift.work_location_id.name or _('Unassigned'))

    @api.depends('start_datetime', 'end_datetime')
    def _compute_planned_hours(self):
        for shift in self:
            if shift.start_datetime and shift.end_datetime:
                delta = shift.end_datetime - shift.start_datetime
                shift.planned_hours = delta.total_seconds() / 3600.0
            else:
                shift.planned_hours = 0.0

    @api.constrains('start_datetime', 'end_datetime')
    def _check_dates(self):
        for shift in self:
            if (shift.start_datetime and shift.end_datetime
                    and shift.end_datetime <= shift.start_datetime):
                raise ValidationError(_('Shift end must be after its start.'))

    @api.constrains('employee_id', 'start_datetime', 'state')
    def _check_max_consecutive_days(self):
        """EO rest-day rule: no more than 6 consecutive work days (rest within 7)."""
        for shift in self:
            if not (shift.employee_id and shift.start_datetime):
                continue
            worked = set(self.search([
                ('employee_id', '=', shift.employee_id.id),
            ]).mapped(lambda s: s.start_datetime.date()))
            day = shift.start_datetime.date()
            run = 1
            probe = day - timedelta(days=1)
            while probe in worked:
                run += 1
                probe -= timedelta(days=1)
            probe = day + timedelta(days=1)
            while probe in worked:
                run += 1
                probe += timedelta(days=1)
            if run > MAX_CONSECUTIVE_WORK_DAYS:
                raise ValidationError(_(
                    '%(name)s would be scheduled for %(n)s consecutive work days.\n\n'
                    'An employee may not work more than %(max)s days in a row — a rest '
                    'day is required within every 7 days (Employment Ordinance).',
                    name=shift.employee_id.name, n=run, max=MAX_CONSECUTIVE_WORK_DAYS))

    @api.constrains('employee_id', 'start_datetime', 'end_datetime')
    def _check_conflicts(self):
        """Block a shift that clashes with the worker's approved leave or another shift."""
        for shift in self:
            if not (shift.employee_id and shift.start_datetime and shift.end_datetime):
                continue
            # approved time off overlapping the shift
            leave = self.env['hr.leave'].sudo().search([
                ('employee_id', '=', shift.employee_id.id),
                ('state', '=', 'validate'),
                ('date_from', '<', shift.end_datetime),
                ('date_to', '>', shift.start_datetime),
            ], limit=1)
            if leave:
                raise ValidationError(_(
                    '%(emp)s is on approved leave (%(lt)s) during this shift.',
                    emp=shift.employee_id.name, lt=leave.holiday_status_id.name))
            # another shift overlapping for the same worker
            other = self.search([
                ('id', '!=', shift.id),
                ('employee_id', '=', shift.employee_id.id),
                ('start_datetime', '<', shift.end_datetime),
                ('end_datetime', '>', shift.start_datetime),
            ], limit=1)
            if other:
                raise ValidationError(_(
                    '%(emp)s already has an overlapping shift (%(s)s).',
                    emp=shift.employee_id.name, s=other.name))

    def action_publish(self):
        self.write({'state': 'published'})
        self.env['hrm.hk.webhook']._dispatch('shift.published', self._shift_payload())

    def action_draft(self):
        self.write({'state': 'draft'})

    def action_confirm(self):
        """Worker acknowledges a published shift."""
        self.filtered(lambda s: s.state == 'published').write({'state': 'confirmed'})

    def _shift_payload(self):
        return [{
            'id': s.id,
            'employee_id': s.employee_id.id,
            'employee': s.employee_id.name,
            'site': s.work_location_id.name,
            'start': s.start_datetime and str(s.start_datetime),
            'end': s.end_datetime and str(s.end_datetime),
            'hours': round(s.planned_hours, 2),
            'state': s.state,
        } for s in self]
