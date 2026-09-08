# -*- coding: utf-8 -*-
from odoo import models


class ReportCustomerStatement(models.AbstractModel):
    """Report parser for the customer statement (客戶月結單).

    When the statement is printed from the wizard, the web /report/download
    controller renders with res_ids=None and carries everything in `data`.
    Without this parser the default rendering context has no `docs` and the
    PDF comes out blank. Resolve the partners from `data['partner_ids']`
    (falling back to docids for the direct /report/pdf/<id> URL).
    """
    _name = 'report.tempeh_cust.report_customer_statement'
    _description = 'Customer Statement Report'

    def _get_report_values(self, docids, data=None):
        data = data or {}
        ids = docids or data.get('partner_ids') or []
        docs = self.env['res.partner'].browse(ids)
        return {
            'doc_ids': ids,
            'doc_model': 'res.partner',
            'docs': docs,
            'data': data,
        }
