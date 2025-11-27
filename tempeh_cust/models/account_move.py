import logging
from odoo import fields, models, api

_logger = logging.getLogger(__name__)

class AccountMove(models.Model):
    _inherit = 'account.move'

    dn_no = fields.Char(string="DN No.")
    attn = fields.Char(string="Attn")

