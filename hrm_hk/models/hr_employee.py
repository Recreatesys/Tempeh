import re

from odoo import api, fields, models, _
from odoo.exceptions import ValidationError

# Hong Kong Identity Card format: 1-2 letters, 6 digits, check digit in ()
HKID_RE = re.compile(r'^[A-Z]{1,2}[0-9]{6}\([0-9A]\)$')


class HrEmployee(models.Model):
    _inherit = 'hr.employee'

    hk_hkid = fields.Char(
        string='HKID',
        groups='hr.group_hr_user',
        help='Hong Kong Identity Card number, e.g. A123456(7).',
    )
    hk_visa_type = fields.Selection(
        selection=[
            ('permanent', 'HK Permanent Resident'),
            ('resident', 'HK Resident (non-permanent)'),
            ('ivas', 'IANG / IVAS'),
            ('gep', 'General Employment Policy (GEP)'),
            ('asmtp', 'ASMTP (Mainland Talents)'),
            ('top', 'Top Talent Pass (TTPS)'),
            ('dependant', 'Dependant Visa'),
            ('other', 'Other Work Visa'),
        ],
        string='Visa / Right to Work',
        groups='hr.group_hr_user',
    )
    hk_visa_number = fields.Char(string='Visa / Work Permit No.', groups='hr.group_hr_user')
    hk_visa_expiry = fields.Date(string='Visa Expiry', groups='hr.group_hr_user')
    hk_visa_expiring_soon = fields.Boolean(
        string='Visa Expiring Soon',
        compute='_compute_hk_visa_expiring_soon',
        help='True when the work visa expires within 60 days.',
    )

    # hr.version doesn't exist in Odoo 18 — these HK statutory fields live on
    # hr.contract (see models/hr_contract.py). Related fields keep them
    # reachable on the employee form (views/hr_employee_views.xml) exactly
    # like the v19 hr.version _inherits delegation did.
    hk_continuous_start_date = fields.Date(
        related='contract_id.hk_continuous_start_date', readonly=False,
        groups='hr.group_hr_user',
    )
    hk_is_continuous_contract = fields.Boolean(
        related='contract_id.hk_is_continuous_contract', readonly=True,
    )
    hk_years_of_service = fields.Float(
        related='contract_id.hk_years_of_service', readonly=True,
    )
    hk_statutory_notice_days = fields.Integer(
        related='contract_id.hk_statutory_notice_days', readonly=False,
        groups='hr.group_hr_user',
    )
    hk_eoy_payment = fields.Selection(
        related='contract_id.hk_eoy_payment', readonly=False,
        groups='hr.group_hr_user',
    )

    @api.depends('hk_visa_expiry')
    def _compute_hk_visa_expiring_soon(self):
        today = fields.Date.today()
        for emp in self:
            emp.hk_visa_expiring_soon = bool(
                emp.hk_visa_expiry and 0 <= (emp.hk_visa_expiry - today).days <= 60
            )

    @api.constrains('hk_hkid')
    def _check_hk_hkid(self):
        for emp in self:
            if emp.hk_hkid and not HKID_RE.match(emp.hk_hkid.strip().upper()):
                raise ValidationError(_(
                    'HKID "%s" is not valid. Expected format like A123456(7).',
                    emp.hk_hkid,
                ))

    hk_is_team_leader = fields.Boolean(
        string='Team Leader', compute='_compute_hk_is_team_leader',
        help='Has direct reports and can oversee/approve them in the web app.')

    @api.depends('child_ids')
    def _compute_hk_is_team_leader(self):
        for emp in self:
            emp.hk_is_team_leader = bool(emp.child_ids)

    def _hrm_hk_managed_employees(self):
        """All employees this one manages (direct + indirect reports), self excluded."""
        self.ensure_one()
        return self.env['hr.employee'].sudo().search(
            [('id', 'child_of', self.id), ('id', '!=', self.id)])

    def _hrm_hk_self_data(self):
        """Compact self-service payload consumed by the employee web app."""
        self.ensure_one()
        emp = self.sudo()
        contract = emp.contract_id
        team = emp._hrm_hk_managed_employees()
        return {
            'id': self.id,
            'name': self.name,
            'work_email': self.work_email,
            'job_title': self.job_title,
            'department': self.department_id.name,
            'manager': self.parent_id.name,
            'mobile': self.mobile_phone,
            'visa_expiry': emp.hk_visa_expiry and str(emp.hk_visa_expiry),
            'is_manager': bool(team),
            'team_count': len(team),
            'contract': {
                'wage': contract.wage,
                'date_start': contract.date_start and str(contract.date_start),
                'continuous': contract.hk_is_continuous_contract,
            } if contract else None,
        }
