from odoo import api, fields, models


class HrWorkEntry(models.Model):
    _inherit = 'hr.work.entry'

    # Make the work-entry Gantt/List (grouped by employee) list EVERY employee
    # as a row, even those with no work entries in the period.
    employee_id = fields.Many2one(group_expand='_read_group_employee_id')

    @api.model
    def _read_group_employee_id(self, employees, domain):
        return self.env['hr.employee'].search(
            [('company_id', 'in', self.env.companies.ids)])
