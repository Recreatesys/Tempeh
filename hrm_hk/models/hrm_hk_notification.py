import logging
from datetime import timedelta

from odoo import api, fields, models

_logger = logging.getLogger(__name__)


class HrmHkNotification(models.Model):
    _name = 'hrm.hk.notification'
    _description = 'HRM Web-App Notification'
    _order = 'create_date desc'

    name = fields.Char(required=True)
    employee_id = fields.Many2one(
        'hr.employee', string='Recipient', required=True, ondelete='cascade', index=True)
    category = fields.Selection(
        [('absence', 'Absence'), ('leave_request', 'Leave Request'),
         ('leave_result', 'Leave Result'), ('info', 'Info')],
        default='info', required=True)
    body = fields.Text()
    related_employee_id = fields.Many2one('hr.employee', string='About')
    res_model = fields.Char()
    res_id = fields.Integer()
    is_read = fields.Boolean(default=False, index=True)
    company_id = fields.Many2one('res.company', default=lambda self: self.env.company)

    @api.model
    def _notify(self, recipient, category, name, body='', about=None,
                res_model=None, res_id=None, webhook_event=None):
        """Create a notification and optionally fan out an outbound webhook."""
        if not recipient:
            return self.browse()
        note = self.sudo().create({
            'employee_id': recipient.id,
            'category': category, 'name': name, 'body': body,
            'related_employee_id': about.id if about else False,
            'res_model': res_model, 'res_id': res_id,
        })
        if webhook_event:
            self.env['hrm.hk.webhook']._dispatch(webhook_event, [{
                'notification_id': note.id,
                'recipient_id': recipient.id, 'recipient': recipient.name,
                'category': category, 'title': name, 'body': body,
                'about_id': about.id if about else None,
                'about': about.name if about else None,
            }])
        return note

    @api.model
    def _cron_check_absences(self):
        """Flag workers scheduled today who have neither clocked in nor are on leave.

        Notifies each absentee's manager (parent_id). Idempotent per day.
        """
        today = fields.Date.today()
        day_start = fields.Datetime.to_datetime(today)
        day_end = day_start + timedelta(days=1)
        shifts = self.env['hrm.hk.shift'].sudo().search([
            ('state', 'in', ['published', 'confirmed']),
            ('start_datetime', '>=', day_start),
            ('start_datetime', '<', day_end),
        ])
        Attendance = self.env['hr.attendance'].sudo()
        Leave = self.env['hr.leave'].sudo()
        flagged = 0
        for emp in shifts.mapped('employee_id'):
            # already notified today?
            if self.sudo().search_count([
                ('category', '=', 'absence'),
                ('related_employee_id', '=', emp.id),
                ('create_date', '>=', day_start)]):
                continue
            present = Attendance.search_count([
                ('employee_id', '=', emp.id), ('check_in', '>=', day_start)])
            if present:
                continue
            on_leave = Leave.search_count([
                ('employee_id', '=', emp.id), ('state', '=', 'validate'),
                ('date_from', '<', day_end), ('date_to', '>=', day_start)])
            if on_leave:
                continue
            manager = emp.parent_id
            if not manager:
                continue
            self._notify(
                manager, 'absence',
                name='%s appears absent today' % emp.name,
                body='%s has a scheduled shift today but has not checked in and is '
                     'not on approved leave.' % emp.name,
                about=emp, res_model='hr.employee', res_id=emp.id,
                webhook_event='absence.detected')
            flagged += 1
        if flagged:
            _logger.info('hrm_hk absence check: flagged %s worker(s)', flagged)
        return flagged
