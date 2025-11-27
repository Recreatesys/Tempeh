from odoo import models, fields, api, _
from datetime import datetime
import logging
from odoo.exceptions import ValidationError
import re

_logger = logging.getLogger(__name__)


class ResCompany(models.Model):
    _inherit = 'res.company'

    company_chop = fields.Binary("Company Chop", attachment=True)
