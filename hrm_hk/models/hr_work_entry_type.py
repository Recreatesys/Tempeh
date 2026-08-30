from odoo import api, fields, models


def _fmt_hour(h):
    """0.0-24.0 float hour -> '9AM' / '2PM' / '12MN'."""
    h = int(round(h)) % 24
    if h == 0:
        return '12MN'
    if h == 12:
        return '12NN'
    suffix = 'AM' if h < 12 else 'PM'
    hour12 = h if 1 <= h <= 12 else h - 12
    return '%d%s' % (hour12, suffix)


class HrWorkEntryType(models.Model):
    _inherit = 'hr.work.entry.type'

    hk_shift = fields.Boolean(string='Is a Shift')
    hk_shift_start = fields.Float(string='Shift Start', help='Start hour, 24h (e.g. 9.0 = 9AM).')
    hk_shift_end = fields.Float(string='Shift End', help='End hour, 24h (e.g. 18.0 = 6PM).')
    hk_overnight = fields.Boolean(
        string='Ends Next Day', help='Shift crosses midnight (e.g. 8PM-4AM).')
    hk_time_display = fields.Char(string='Hours', compute='_compute_hk_time_display')
    hk_display_code = fields.Char(
        string='Display Code',
        help='Short label shown on rosters/reports (e.g. "A", "B", "C").')

    hk_pay_mode = fields.Selection(
        [('multiplier', 'Multiplier of hourly wage'),
         ('hourly', 'Fixed amount per hour')],
        string='Shift Pay', default='multiplier')
    # hr.work.entry.type has no native pay-rate field in this Odoo version
    # (no amount_rate), so this is a plain module-owned field rather than a
    # related one.
    hk_pay_multiplier = fields.Float(
        string='Pay Multiplier', default=1.0,
        help='e.g. 1.5 pays 1.5× the normal hourly wage for this shift.')
    hk_hourly_amount = fields.Monetary(
        string='Amount / Hour', currency_field='currency_id',
        help='Fixed pay per hour worked on this shift (overrides the multiplier).')
    currency_id = fields.Many2one(
        'res.currency', default=lambda self: self.env.company.currency_id)

    @api.depends('hk_shift', 'hk_shift_start', 'hk_shift_end', 'hk_overnight')
    def _compute_hk_time_display(self):
        for rec in self:
            if rec.hk_shift:
                tail = ' (next day)' if rec.hk_overnight else ''
                rec.hk_time_display = '%s-%s%s' % (
                    _fmt_hour(rec.hk_shift_start), _fmt_hour(rec.hk_shift_end), tail)
            else:
                rec.hk_time_display = False

    def hk_effective_hourly(self, base_hourly):
        """Effective pay per hour for this shift given the worker's base hourly wage."""
        self.ensure_one()
        if self.hk_pay_mode == 'hourly' and self.hk_hourly_amount:
            return self.hk_hourly_amount
        return base_hourly * (self.hk_pay_multiplier or 1.0)
