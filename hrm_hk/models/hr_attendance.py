from odoo import api, fields, models


class HrAttendance(models.Model):
    _inherit = 'hr.attendance'

    hk_site_id = fields.Many2one(
        'hr.work.location', string='Site (QR)', readonly=True, index=True,
        help='Site whose entrance QR was scanned for this clock in/out.')
    hk_in_map = fields.Char(string='Check-in location', compute='_compute_hk_maps')
    hk_out_map = fields.Char(string='Check-out location', compute='_compute_hk_maps')

    @api.depends('in_latitude', 'in_longitude', 'out_latitude', 'out_longitude')
    def _compute_hk_maps(self):
        for r in self:
            r.hk_in_map = ('https://maps.google.com/?q=%s,%s' % (r.in_latitude, r.in_longitude)
                           if r.in_latitude else False)
            r.hk_out_map = ('https://maps.google.com/?q=%s,%s' % (r.out_latitude, r.out_longitude)
                            if r.out_latitude else False)
