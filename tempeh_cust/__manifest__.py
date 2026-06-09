# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

{
    'name': 'Tempeh Customization',
    'version': '18.0.1.1.0',
    'summary': 'Contains customization for Tempeh',
    'description': """
    """,
    'author': 'Lau Siu Hin',
    'website': '',
    'depends': ['contacts', 'base', 'web', 'account', 'sale_management'],
    'data': [
        "security/ir.model.access.csv",
        "views/invoice_inherit.xml",
        "report/report.xml",
        "report/sale_order_report.xml",
        "report/customer_statement_report.xml",
        "report/customer_statement_template.xml",
        "views/product_template.xml",
        "views/res_company_view.xml",
        "views/account_move_view.xml",
        "wizard/customer_statement_wizard_views.xml",
        "views/customer_statement_menu.xml",
    ],

    'installable': True,
    'application': False,
    'auto_install': False,
}
