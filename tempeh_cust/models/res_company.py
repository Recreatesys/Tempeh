from odoo import models, fields, api, _
from datetime import datetime
import logging
from odoo.exceptions import ValidationError
import re

_logger = logging.getLogger(__name__)


class ResCompany(models.Model):
    _inherit = 'res.company'

    company_chop = fields.Binary("Company Chop", attachment=True)

    # Default Terms & Conditions (payment remarks) shown/editable on the
    # sales order (note) and invoice (narration) forms and printed reports.
    _TEMPEH_DEFAULT_TERMS = (
        "Remarks:\n"
        "1. Cheque Should be crossed and made payable to: Srikandi Food Company Limited\n"
        "2. HSBC account: 582-413910-838 (FPS 103409777)"
    )

    @api.model
    def _tempeh_init_invoice_terms(self):
        """Set the default Terms & Conditions once, and enable the feature so
        new sales orders/invoices pre-fill their editable T&C field.
        Guarded by a config flag so it never clobbers later manual edits."""
        icp = self.env['ir.config_parameter'].sudo()
        if icp.get_param('tempeh_cust.terms_initialized'):
            return
        icp.set_param('account.use_invoice_terms', 'True')
        self.env['res.company'].sudo().search([]).write({
            'terms_type': 'plain',
            'invoice_terms': self._TEMPEH_DEFAULT_TERMS,
        })
        icp.set_param('tempeh_cust.terms_initialized', '1')
