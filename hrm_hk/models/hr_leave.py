from odoo import models


class HrLeave(models.Model):
    _inherit = 'hr.leave'

    def action_approve(self, check_state=True):
        res = super().action_approve(check_state=check_state)
        self.env['hrm.hk.webhook']._dispatch('leave.approved', self._hrm_hk_payload())
        return res

    def action_refuse(self):
        res = super().action_refuse()
        self.env['hrm.hk.webhook']._dispatch('leave.refused', self._hrm_hk_payload())
        return res

    def _hrm_hk_payload(self):
        return [{
            'id': leave.id,
            'employee_id': leave.employee_id.id,
            'employee': leave.employee_id.name,
            'leave_type': leave.holiday_status_id.name,
            'date_from': leave.request_date_from and str(leave.request_date_from),
            'date_to': leave.request_date_to and str(leave.request_date_to),
            'number_of_days': leave.number_of_days,
            'state': leave.state,
        } for leave in self]
