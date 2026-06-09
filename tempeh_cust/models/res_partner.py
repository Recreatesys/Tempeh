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
        move_type_labels = dict(self.env['account.move']._fields['move_type']._description_selection(self.env))
        for aml in period_amls:
            running += aml.balance
            move = aml.move_id
            lines.append({
                'date': aml.date,
                'date_str': self._format_cn_date(aml.date),
                'move_type': move_type_labels.get(move.move_type, '') if move else '',
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
