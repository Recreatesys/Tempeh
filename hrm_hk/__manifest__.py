{
    'name': 'Hong Kong HR Management',
    'version': '18.0.1.0.0',
    'category': 'Human Resources',
    'summary': 'Hong Kong labour-law HR layer + employee web-app REST API',
    'description': """
Hong Kong HR Management (hrm_hk)
================================
Thin customisation layer on top of Odoo's native Hong Kong payroll
localization (l10n_hk_hr_payroll). Adds:

* HK-specific employee & contract fields (HKID, visa/work permit,
  continuous-contract "418" flag, statutory notice, end-of-year payment).
* Statutory leave types & accrual plans (annual leave 7->14, sickness
  2->4/month cap 120, maternity 14 weeks, paternity 5 days).
* Hong Kong working calendar, rest days, and statutory holidays (2026: 15).
* A versioned REST API (/api/hrm/v1) + JWT auth so an external
  employee web app (Next.js) can 2-way integrate with Odoo.
* Outbound webhooks (leave approved/rejected, payslip posted).

Statutory engine (MPF, 713/ADW, IR56, severance/LSP, eMPF) is provided by
the native localization and intentionally NOT reimplemented here.
""",
    'author': 'Recreate',
    'website': 'https://www.labour.gov.hk/eng/public/ConciseGuide.htm',
    'depends': [
        'hr',
        'hr_holidays',
        'hr_attendance',
        'hr_recruitment',
        'hr_appraisal',
        'hr_expense',
        'hr_skills',
        'web_gantt',
        'l10n_hk',
        'l10n_hk_hr_payroll',
        # 'l10n_hk_hr_payroll_empf',  # eMPF export — enable once present in the Enterprise source
        # 'documents_l10n_hk_hr_payroll' dropped: it pulls the enterprise Documents
        # module, which is broken in this source (community/enterprise skew: base
        # 'kpi.provider' model missing). hrm_hk uses no Documents features; the HK
        # payroll engine + IR56 models come from l10n_hk_hr_payroll above.
    ],
    'data': [
        'security/hrm_hk_groups.xml',
        'security/ir.model.access.csv',
        'data/resource_calendar_hk.xml',
        'data/hr_leave_type_data.xml',
        'data/hr_leave_accrual_data.xml',
        'data/hk_public_holidays_2026.xml',
        'data/hr_work_entry_type_data.xml',
        'data/ir_config_parameter.xml',
        'data/ir_cron.xml',
        'data/hrm_hk_ess_portlet_config_data.xml',
        'views/hr_employee_views.xml',
        'views/hr_work_entry_type_views.xml',
        'views/hr_work_location_views.xml',
        'views/hrm_hk_attendance_audit_views.xml',
        'views/hrm_hk_api_key_views.xml',
        'views/hrm_hk_webhook_views.xml',
        'views/hrm_hk_shift_views.xml',
        'views/hrm_hk_notice_views.xml',
        'views/hrm_hk_holiday_lieu_views.xml',
        'views/hrm_hk_ess_request_views.xml',
        'views/hrm_hk_menus.xml',
    ],
    'installable': True,
    'application': True,
    'auto_install': False,
    'license': 'LGPL-3',
}
