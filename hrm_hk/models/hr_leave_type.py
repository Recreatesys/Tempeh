from odoo import fields, models


class HrLeaveType(models.Model):
    _inherit = 'hr.leave.type'

    hk_statutory = fields.Boolean(
        string='HK Statutory Leave',
        help='Leave mandated by the Employment Ordinance (annual, sickness, '
             'maternity, paternity, statutory holidays).',
    )
    hk_pay_basis = fields.Selection(
        selection=[
            ('full', 'Full Wages'),
            ('adw_713', 'Average Daily Wages (713)'),
            ('unpaid', 'Unpaid'),
        ],
        string='Pay Basis',
        default='full',
        help='How the leave is paid. Statutory entitlements are paid on the '
             '12-month average daily wages ("713 rule").',
    )
    hk_pay_rate = fields.Float(
        string='Pay Rate',
        default=1.0,
        help='Fraction of the pay basis paid, e.g. 0.8 (4/5) for sickness '
             'allowance and paternity leave.',
    )
    hk_requires_proof = fields.Boolean(
        string='Requires Medical Proof',
        help='When set, the employee web app requires a supporting document '
             '(e.g. a doctor\'s note / 醫生紙) to be uploaded before a request '
             'of this type can be submitted.',
    )
