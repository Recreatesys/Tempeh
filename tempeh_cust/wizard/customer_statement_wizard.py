# -*- coding: utf-8 -*-
import base64

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

    def _build_statement_filename(self):
        """客戶月結單-MMYYYY-客戶名,客戶名,... (period taken from the End Date)."""
        period = (self.end_date or fields.Date.context_today(self)).strftime('%m%Y')

        def clean(name):
            name = name or "Unnamed"
            for ch in '/\\:*?"<>|':
                name = name.replace(ch, '-')
            return name.strip()

        names = ",".join(clean(p.name) for p in self.partner_ids)
        base = f"客戶月結單-{period}-{names}"
        # Keep within a filesystem-safe length when many customers are selected.
        if len(base) > 180:
            base = base[:180].rstrip(",") + "等"
        return base + ".pdf"

    def action_print_customer_statements(self):
        self.ensure_one()
        if not self.partner_ids:
            raise UserError(_("Please select at least one customer."))
        if self.start_date > self.end_date:
            raise UserError(_("Start Date must be on or before End Date."))
        data = {
            # The report parser reads the partner ids / dates from data.
            'partner_ids': self.partner_ids.ids,
            'start_date': fields.Date.to_string(self.start_date),
            'end_date': fields.Date.to_string(self.end_date),
        }
        # Render the PDF ourselves and serve it as an attachment so the download
        # filename can be 客戶月結單-MMYYYY-客戶名,... (the core /report/download
        # path only honours print_report_name for a single record, and falls
        # back to the static report name for a multi-customer PDF).
        report = self.env.ref('tempeh_cust.action_report_customer_statement')
        pdf_content, _dummy = report._render_qweb_pdf(
            report.report_name, self.partner_ids.ids, data=data)
        attachment = self.env['ir.attachment'].create({
            'name': self._build_statement_filename(),
            'type': 'binary',
            'datas': base64.b64encode(pdf_content),
            'mimetype': 'application/pdf',
            'res_model': self._name,
            'res_id': self.id,
        })
        return {
            'type': 'ir.actions.act_url',
            'url': '/web/content/%s?download=true' % attachment.id,
            'target': 'self',
        }
