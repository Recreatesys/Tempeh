from odoo import api, fields, models


class HrContract(models.Model):
    # Odoo 18 uses hr.contract (hr.version / _inherits delegation is v19-only).
    _inherit = 'hr.contract'

    # Continuous employment can predate this contract (e.g. renewals / promotions).
    hk_continuous_start_date = fields.Date(
        string='Continuous Service Since',
        groups='hr.group_hr_user',
        help='Start of the continuous period of employment used for statutory '
             'entitlements (annual leave scale, severance / long-service payment). '
             'Defaults to the contract start when empty.',
    )
    hk_is_continuous_contract = fields.Boolean(
        string='Continuous Contract (418)',
        compute='_compute_hk_is_continuous_contract',
        help='Employee has worked 4+ consecutive weeks at 18+ hours/week and is '
             'therefore under a continuous contract per the Employment Ordinance.',
    )
    hk_years_of_service = fields.Float(
        string='Years of Service',
        compute='_compute_hk_years_of_service',
    )
    hk_statutory_notice_days = fields.Integer(
        string='Notice Period (days)',
        default=30,
        groups='hr.group_hr_user',
        help='Contractual termination notice. EO minimum is 7 days after '
             'probation for a continuous contract (or as agreed, not less than statutory).',
    )
    hk_eoy_payment = fields.Selection(
        selection=[
            ('none', 'None'),
            ('one_month', 'One Month (13th month)'),
            ('contractual', 'Per Contract Terms'),
        ],
        string='End of Year Payment',
        default='none',
        groups='hr.group_hr_user',
    )

    @api.depends('date_start', 'hk_continuous_start_date',
                 'resource_calendar_id.attendance_ids')
    def _compute_hk_is_continuous_contract(self):
        today = fields.Date.today()
        for contract in self:
            start = contract.hk_continuous_start_date or contract.date_start
            weeks = ((today - start).days / 7.0) if start else 0.0
            calendar = contract.resource_calendar_id
            hours = sum(
                (a.hour_to - a.hour_from) for a in calendar.attendance_ids
            ) if calendar else 0.0
            contract.hk_is_continuous_contract = weeks >= 4.0 and hours >= 18.0

    @api.depends('date_start', 'hk_continuous_start_date', 'date_end')
    def _compute_hk_years_of_service(self):
        today = fields.Date.today()
        for contract in self:
            start = contract.hk_continuous_start_date or contract.date_start
            end = contract.date_end or today
            contract.hk_years_of_service = max(0.0, (end - start).days / 365.25) if start else 0.0

    def hk_statutory_annual_leave_days(self):
        """Annual leave entitlement per EO s.41AA.

        Years 1-2: 7 days; then +1/year (yr3=8 ... yr9=14); capped at 14.
        Entitlement requires 12 months of continuous service.
        """
        self.ensure_one()
        n = int(self.hk_years_of_service)
        if n < 1:
            return 0
        if n <= 2:
            return 7
        return min(14, n + 5)
