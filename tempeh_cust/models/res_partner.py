# -*- coding: utf-8 -*-
from odoo import models, fields, api, _


class ResPartner(models.Model):
    _inherit = 'res.partner'

    # Self-reference so the QWeb contact widget can render this partner's
    # name + address + VAT block (mirrors the wangchao statement header).
    statement_partner_id = fields.Many2one(
        'res.partner', compute='_compute_statement_partner_id', string='Statement Partner')

    def _compute_statement_partner_id(self):
        for rec in self:
            rec.statement_partner_id = rec

    def open_customer_statement(self):
        """Wire the native 'Customer Statement' button (account_followup /
        account_reports) and the Follow-up Reports (催款報表) flow to our
        custom 客戶月結單: open the date-range wizard for the selected
        customer(s) instead of the standard dynamic report."""
        return {
            'name': '客戶月結單',
            'type': 'ir.actions.act_window',
            'res_model': 'customer.statement.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_partner_ids': [(6, 0, self.ids)]},
        }

    # Outstanding customer invoices shown on the 催款報表 per-customer form.
    # Writable (inverse) so the 不納入後續行動 toggle can be saved inline.
    statement_move_line_ids = fields.One2many(
        'account.move.line', compute='_compute_statement_move_line_ids',
        inverse='_inverse_statement_move_line_ids', readonly=False,
        string='Outstanding Invoices')

    def _compute_statement_move_line_ids(self):
        AML = self.env['account.move.line']
        for p in self:
            p.statement_move_line_ids = AML.search([
                ('partner_id', '=', p.id),
                ('parent_state', '=', 'posted'),
                ('account_id.account_type', '=', 'asset_receivable'),
                ('amount_residual', '!=', 0),
            ], order='date_maturity, date')

    def _inverse_statement_move_line_ids(self):
        """Persist inline edits made on the 催款報表 form (the 不納入後續行動
        toggle). The ORM applies the x2many child writes when the field value is
        set; nothing further is required here — the recordset itself is derived
        by search and must not be rewritten."""
        return

    def action_print_customer_statement(self):
        """Per-customer 'Print Statement' button on the 催款報表 form."""
        self.ensure_one()
        return self.open_customer_statement()

    def action_open_partner_form(self):
        """客戶 smart button — open the standard contact form for this customer
        (an explicit view so we do not recurse into the 催款報表 form)."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': self.name,
            'res_model': 'res.partner',
            'res_id': self.id,
            'view_mode': 'form',
            'views': [(self.env.ref('base.view_partner_form').id, 'form')],
            'target': 'current',
        }

    def _get_statement_report_name(self):
        """Filename for the printed customer statement (催款報表 / 客戶月結單)."""
        if len(self) == 1:
            name = self.name or "Unnamed"
            for char in '/\\:*?"<>|':
                name = name.replace(char, '-')
            return f"客戶月結單 - {name}"
        return "客戶月結單"

    @api.model
    def _format_cn_date(self, value):
        """Return a date as 'YYYY年M月D日' (empty string when no value)."""
        if not value:
            return ""
        d = fields.Date.to_date(value)
        return f"{d.year}年{d.month}月{d.day}日"

    def _get_customer_statement_lines(self, start_date=None, end_date=None):
        """Build the customer statement data for a single partner.

        Reproduces the wangchao 客戶月結單 figures natively on v18 by reading the
        partner's posted receivable journal items (no dependency on the dropped
        l10n_account_customer_statements module).
        """
        self.ensure_one()
        # Defaults: current month to today (mirrors the wizard defaults).
        today = fields.Date.context_today(self)
        start_date = fields.Date.to_date(start_date) if start_date else today.replace(day=1)
        end_date = fields.Date.to_date(end_date) if end_date else today

        company = self.env.company
        AML = self.env['account.move.line']
        base_domain = [
            ('parent_state', '=', 'posted'),
            ('account_id.account_type', '=', 'asset_receivable'),
            ('partner_id', '=', self.id),
            ('company_id', '=', company.id),
        ]

        # 初期未付餘額 — opening balance from everything before the period.
        opening_amls = AML.search(base_domain + [('date', '<', start_date)])
        opening = sum(opening_amls.mapped('balance'))

        # Period movements with a running cumulative unpaid balance.
        period_amls = AML.search(
            base_domain + [('date', '>=', start_date), ('date', '<=', end_date)],
            order='date, id',
        )
        running = opening
        lines = []
        short_type = {'out_invoice': 'Invoice', 'out_refund': 'Credit Note',
                      'in_invoice': 'Vendor Bill', 'in_refund': 'Vendor Credit Note'}
        for aml in period_amls:
            running += aml.balance
            move = aml.move_id
            lines.append({
                'date': aml.date,
                'date_str': self._format_cn_date(aml.date),
                'move_type': (short_type.get(move.move_type, '') if move else ''),
                'activity': (move.name if move and move.name and move.name != '/' else aml.name) or '',
                'reference': (move.ref or '') if move else '',
                'due_date_str': self._format_cn_date(aml.date_maturity),
                'amount': aml.balance,
                'balance': running,
            })

        return {
            'partner': self,
            'company': company,
            'currency': company.currency_id,
            'start_date': start_date,
            'end_date': end_date,
            'start_date_str': self._format_cn_date(start_date),
            'end_date_str': self._format_cn_date(end_date),
            'opening': opening,
            'lines': lines,
            'closing': running,
        }


class AccountMoveLine(models.Model):
    _inherit = 'account.move.line'

    # Columns surfaced on the 催款報表 per-customer form so it mirrors the native
    # follow-up detail. `invoice_origin` (原始) is already provided by
    # account_followup; these add 預計交貨日期 and the 不納入後續行動 toggle.
    statement_delivery_date = fields.Date(
        related='move_id.delivery_date', string='預計交貨日期', readonly=True)
    statement_excluded = fields.Boolean(
        string='不納入後續行動', default=False,
        help="剔選後此發票標示為不納入催款後續行動。")
