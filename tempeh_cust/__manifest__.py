# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

{
    'name': 'Tempeh Customization',
    'version': '1.0',
    'summary': 'Contains customization for Tempeh',
    'description': """
    """,
    'author': 'Lau Siu Hin',
    'website': '',
    'depends': ['contacts', 'base', 'web', 'account', 'sale_management'],
    'data': [
        "views/invoice_inherit.xml",
        "report/report.xml",
        "report/sale_order_report.xml",
        "views/product_template.xml",
        "views/res_company_view.xml",
        "views/account_move_view.xml",
    ],

    'installable': True,
    'application': False,
    'auto_install': False,
}
