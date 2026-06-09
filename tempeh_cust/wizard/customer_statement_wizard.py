# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import UserError


class CustomerStatementWizard(models.TransientModel):
    _name = 'customer.statement.wizard'
    _description = 'Customer Statement Wizard'

    start_date = fields.Date(
        string="Start Date", required=True,
        default=lambda self: fields.Date.context_today(self).replace(day=1))
    end_date = fields.Date(
        string="End Date", required=True,
        default=lambda self: fields.Date.context_today(self))
    partner_ids = fields.Many2many('res.partner', string="Customers")

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        # When launched from the res.partner Action menu, preselect the records.
        if self.env.context.get('active_model') == 'res.partner':
            active_ids = self.env.context.get('active_ids', [])
            if active_ids and 'partner_ids' in fields_list:
                res['partner_ids'] = [(6, 0, active_ids)]
        return res

    def action_print_customer_statements(self):
        self.ensure_one()
        if not self.partner_ids:
            raise UserError(_("Please select at least one customer."))
        if self.start_date > self.end_date:
            raise UserError(_("Start Date must be on or before End Date."))
        data = {
            'start_date': fields.Date.to_string(self.start_date),
            'end_date': fields.Date.to_string(self.end_date),
        }
        return self.env.ref('tempeh_cust.action_report_customer_statement').report_action(
            self.partner_ids, data=data)
