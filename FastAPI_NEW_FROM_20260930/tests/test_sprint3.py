"""
Database-free regression checks.

Run from the application directory:
    python -m unittest discover -s tests -v

These tests validate generated SQL and mandatory inputs.
They do not verify warehouse access or actual view schemas.
"""

import unittest

from params import (
    GlobalParams,
    MissingParameters,
    MultiValue,
    build_converting_query,
    build_papermaking_query,
)


def multi(*items):
    return MultiValue(values=list(items))


class PapermakingQueryTests(unittest.TestCase):
    def test_pm_summary_uses_pm_view_and_line_column(self):
        params = GlobalParams(
            plant_code_name="ESF1 - Valls",
            pm_line=multi("PM4"),
        )

        query, binds = build_papermaking_query(
            "pm_report_3", params, 10, 0
        )

        self.assertIn(
            "FROM traceability_poc.v_PM_ProdOrders WHERE", query
        )
        self.assertIn("PM_ProductionLine IN", query)
        self.assertNotIn("CMB_ProductionLine", query)
        self.assertIn("PM4", binds.values())

    def test_pm_details_uses_details_view_batch_and_sscc(self):
        params = GlobalParams(
            plant_code_name="ESF1 - Valls",
            pm_line=multi("PM4"),
            pm_reel_batch_id=multi("BATCH123"),
            pm_produced_sscc=multi("000012345678901234"),
        )

        query, binds = build_papermaking_query(
            "pm_report_4", params, 10, 0
        )

        self.assertIn(
            "FROM traceability_poc.v_PM_ProdOrders_details WHERE",
            query,
        )
        self.assertIn("BatchId IN", query)
        self.assertIn("SSCC IN", query)
        self.assertNotIn("ReelBatchId", query)
        self.assertNotIn("ProducedSSCC", query)
        self.assertIn("000012345678901234", binds.values())

    def test_combiner_details_requires_sscc_or_order(self):
        params = GlobalParams(
            plant_code_name="ESF1 - Valls",
            cmb_line=multi("CMB1"),
        )

        with self.assertRaises(MissingParameters):
            build_papermaking_query(
                "cmb_report_2", params, 10, 0
            )

    def test_combiner_details_prioritizes_sscc(self):
        params = GlobalParams(
            plant_code_name="ESF1 - Valls",
            cmb_line=multi("CMB1"),
            cmb_order_number=multi("12345678"),
            cmb_produced_sscc=multi("000098765432109876"),
        )

        query, binds = build_papermaking_query(
            "cmb_report_2", params, 10, 0
        )

        self.assertIn(
            "FROM traceability_poc.v_CMB_ProdOrders_details WHERE",
            query,
        )
        self.assertIn("SSCC IN", query)
        self.assertNotIn("OrderNumber IN", query)
        self.assertNotIn("12345678", binds.values())

    def test_summary_datetime_range_is_continuous(self):
        params = GlobalParams(
            plant_code_name="ESF1 - Valls",
            pm_line=multi("PM4"),
            pm_prod_date_from="2026-09-01",
            pm_prod_time_from="22:00",
            pm_prod_date_to="2026-09-02",
            pm_prod_time_to="06:00",
        )

        query, binds = build_papermaking_query(
            "pm_report_3", params, 10, 0
        )

        self.assertIn(
            "CAST(ProdStartDateTime AS TIMESTAMP) >=", query
        )
        self.assertNotIn("substr(", query)
        self.assertEqual(binds["pm_from"], "2026-09-01 22:00:00")
        self.assertEqual(binds["pm_to"], "2026-09-02 06:01:00")

    def test_pm_search_does_not_require_combiner_line(self):
        params = GlobalParams(
            plant_code_name="ESF1 - Valls",
            pm_line=multi("PM4"),
        )

        query, _ = build_papermaking_query(
            "pm_report_3", params, 10, 0
        )
        self.assertIn("v_PM_ProdOrders", query)


class ConvertingQueryTests(unittest.TestCase):
    def test_synchronization_uses_full_production_order_subquery(self):
        params = GlobalParams(
            plant_code_name="ESF1 - Valls",
            converting_production_line=multi("A5T - INTEGRA"),
            zfin_material_id=multi("32539914"),
            zfin_order=multi("102634619"),
        )

        query, _ = build_converting_query(
            "summary",
            params,
            10,
            0,
            apply_order_link_filter=True,
        )

        self.assertIn(
            "SELECT DISTINCT OrderNumber FROM "
            "traceability_poc.v_ZFIN_ProdOrders",
            query,
        )

        # Only the outer paginated report has a LIMIT.
        self.assertEqual(query.count(" LIMIT "), 1)


if __name__ == "__main__":
    unittest.main()