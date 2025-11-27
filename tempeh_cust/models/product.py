import logging
from odoo import fields, models, api

_logger = logging.getLogger(__name__)

class ProductTemplate(models.Model):
    _inherit = 'product.template'

    best_before_date = fields.Date(string="BBD")

