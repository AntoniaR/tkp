import unittest
import tkp.steps.feature_extraction
from tkp.database import query
from tkp.classification.transient import Transient
from tkp.testutil import db_subs
from tkp.testutil.decorators import requires_database

@requires_database()
class TestFeatureExtraction(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dataset_id = db_subs.create_dataset_8images(extract_sources=True)
        runcat_query = "select id from runningcatalog where dataset=%s"
        cursor = query(runcat_query, [cls.dataset_id])
        cls.transients = [Transient(runcatid=i) for (i,) in cursor.fetchall()]

    @unittest.skip("TODO: extract_features recipe needs modification!!!")
    def test_extract_features(self):
        transient = self.transients[0]
        tkp.steps.feature_extraction.extract_features(transient)