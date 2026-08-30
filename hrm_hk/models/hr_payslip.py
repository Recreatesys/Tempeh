import base64

from odoo import models

# Prefer the Hong Kong payslip layout, fall back to the generic one.
_HK_REPORT = 'l10n_hk_hr_payroll.action_report_payslip_hk'
_GEN_REPORT = 'hr_payroll.action_report_payslip'
_ATT_PREFIX = 'HRMPayslip-'


class HrPayslip(models.Model):
    _inherit = 'hr.payslip'

    def action_payslip_done(self):
        res = super().action_payslip_done()
        self.env['hrm.hk.webhook']._dispatch('payslip.posted', [{
            'id': slip.id,
            'employee_id': slip.employee_id.id,
            'employee': slip.employee_id.name,
            'period': slip.date_from and slip.date_to and
            f'{slip.date_from} - {slip.date_to}',
            'net': slip._get_line_values(['NET']).get('NET', {}).get(slip.id, {}).get('total')
            if hasattr(slip, '_get_line_values') else None,
        } for slip in self])
        # cache the rendered PDF now (posted payslips are immutable) so the web
        # app serves a stored copy instead of re-rendering per download.
        for slip in self:
            slip._hrm_hk_render_pdf(force=True)
        return res

    def _hrm_hk_pdf_attachment(self):
        self.ensure_one()
        return self.env['ir.attachment'].sudo().search([
            ('res_model', '=', 'hr.payslip'), ('res_id', '=', self.id),
            ('name', '=like', _ATT_PREFIX + '%')], limit=1)

    def _hrm_hk_render_pdf(self, force=False):
        """Return the payslip PDF bytes, caching them as an attachment.

        Rendered here (a warm HR-user backend context) rather than per web
        request, which avoids a cold-worker QWeb warmup that blanks the
        group-restricted employee fields.
        """
        self.ensure_one()
        att = self._hrm_hk_pdf_attachment()
        if att and not force:
            return base64.b64decode(att.datas)
        report_xmlid = _HK_REPORT
        report = self.env.ref(report_xmlid, raise_if_not_found=False)
        if not report:
            report_xmlid = _GEN_REPORT
            report = self.env.ref(report_xmlid, raise_if_not_found=False)
        if not report:
            return None
        # prime group cache for field visibility, then render
        self.env.user.has_group('hr_payroll.group_hr_payroll_user')
        pdf, _ct = report.sudo()._render_qweb_pdf(report_xmlid, self.ids)
        if att:
            att.sudo().write({'datas': base64.b64encode(pdf)})
        else:
            self.env['ir.attachment'].sudo().create({
                'name': '%s%s.pdf' % (_ATT_PREFIX, self.id),
                'res_model': 'hr.payslip', 'res_id': self.id,
                'type': 'binary', 'mimetype': 'application/pdf',
                'datas': base64.b64encode(pdf),
            })
        return pdf
