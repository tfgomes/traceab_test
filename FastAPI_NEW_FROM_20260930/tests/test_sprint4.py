"""
Database-free regression checks for the Sprint 4 master / slave engine.

Run from the application directory:
    python -m unittest discover -s tests -v
"""

import unittest

from params import (
    GlobalParams,
    MissingParameters,
    MultiValue,
    ReportCatalog,
    build_parameter_definitions,
    build_query,
)


def multi(*items):
    return MultiValue(values=list(items))


def row(fid, page, section, table, column, master, pid=None, up="", down="", mandatory=0, show=1, datatype="string"):
    return {
        "FieldID": fid, "Page": page, "Section": section, "Table": table, "Column": column,
        "Name": column, "Datatype": datatype, "ParameterID": pid, "ParameterIDmax": None,
        "Mandatory": mandatory, "Upsynchwith": up, "Downsynchwith": down, "Show": show,
        "isMasterTable": master,
    }


def catalog():
    return ReportCatalog([
        # p3 = PM details (master), p2 = PM summary (slave)
        row("pm_d_plant", "papermaking", "p3", "v_PM_ProdOrders_details", "PlantId", 1, "pm-p1", mandatory=1),
        row("pm_d_batch", "papermaking", "p3", "v_PM_ProdOrders_details", "BatchId", 1, "pm-p5", up="cmb_d_mr"),
        row("pm_d_order", "papermaking", "p3", "v_PM_ProdOrders_details", "OrderNumber", 1),
        row("pm_s_order", "papermaking", "p2", "v_PM_ProdOrders", "OrderNumber", 0, down="pm_d_order"),
        # p5 = CMB details (master)
        row("cmb_d_mr", "papermaking", "p5", "v_CMB_ProdOrders_details", "MRBatchId", 1, down="pm_d_batch"),
        row("cmb_d_batch", "papermaking", "p5", "v_CMB_ProdOrders_details", "BatchId", 1),
    ])


class MasterSlaveTests(unittest.TestCase):
    def sql(self, report, sync_mode, direction="down", **params):
        query, binds, meta = build_query(
            catalog(), "papermaking", report, GlobalParams(**params),
            sync_mode=sync_mode, flow_direction=direction,
        )
        return query, binds, meta

    def test_unsync_master_uses_globals_only(self):
        query, binds, _ = self.sql("p5", "unsync")
        self.assertNotIn("v_PM_ProdOrders_details", query)

    def test_unsync_requires_mandatory_on_master(self):
        with self.assertRaises(MissingParameters):
            self.sql("p3", "unsync")

    def test_sync_has_no_mandatory(self):
        query, _, _ = self.sql("p3", "sync")
        self.assertIn("v_PM_ProdOrders_details", query)

    def test_slave_follows_master_in_unsync(self):
        query, binds, _ = self.sql("p2", "unsync", plant_code_name="ESF3")
        self.assertIn("v_PM_ProdOrders_details", query)
        self.assertIn("`OrderNumber`", query)
        self.assertIn("ESF3", binds.values())

    def test_slave_has_no_own_mandatory(self):
        # master mandatory still applies through the master
        with self.assertRaises(MissingParameters):
            self.sql("p2", "unsync")

    def test_master_chain_only_in_sync_direction(self):
        query, _, _ = self.sql("p5", "sync", "down", plant_code_name="ESF3", pm_reel_batch_id=multi("9720365630"))
        self.assertIn("v_PM_ProdOrders_details", query)
        self.assertIn("9720365630", self.sql("p5", "sync", "down", pm_reel_batch_id=multi("9720365630"))[1].values())

    def test_master_ignores_other_direction(self):
        query, _, _ = self.sql("p5", "sync", "up")
        self.assertNotIn("v_PM_ProdOrders_details", query)


class ParameterHeaderTests(unittest.TestCase):
    def test_page_section_headers(self):
        defs = build_parameter_definitions([
            {"Page": "Papermaking", "Section": "PM production", "ID": "pm-p1", "Name": "Plant",
             "Format": "dropdown", "Mandatory": 1},
        ])
        group = defs.profiles[0]
        self.assertEqual(group["title"], "Papermaking Parameters")
        self.assertEqual(group["sections"][-1]["title"], "PM production")

    def test_old_headers_still_read(self):
        defs = build_parameter_definitions([
            {"Section": "Papermaking", "SubSection": "PM production", "ID": "pm-p1", "Name": "Plant"},
        ])
        self.assertEqual(defs.profiles[0]["sections"][-1]["title"], "PM production")


if __name__ == "__main__":
    unittest.main()