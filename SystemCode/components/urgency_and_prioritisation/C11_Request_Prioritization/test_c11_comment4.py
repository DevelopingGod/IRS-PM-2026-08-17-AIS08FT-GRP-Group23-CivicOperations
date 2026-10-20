"""Run from C11 folder with sibling C10_Urgency_Assignment folder."""
import csv
import unittest
from pathlib import Path
from c11_final import prioritize_from_c10
from c11_sla_lookup import load_official, load_review, lookup

ROOT = Path(__file__).resolve().parent
NOW = '2026-10-13T12:00:00+00:00'

class Comment4Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with (ROOT / 'demo_scenarios.csv').open(encoding='utf-8-sig', newline='') as f:
            cls.demo = list(csv.DictReader(f))
        cls.review = load_review(ROOT / 'c11_water_sewer_SLA_detail_review.csv')
        cls.official = load_official(ROOT / 'nyc311_official_sla.csv')

    def base(self, code=None):
        candidate = next((x for x in self.demo if x['request_type_code'] == code), self.demo[0])
        return dict(candidate, request_id='TEST-04', agency=candidate['expected_agency'],
                    emergency_action='CONTINUE', created_date='2026-10-12T12:00:00+00:00')

    def test_no_guess_from_rt_code(self):
        rows = [dict(x, request_id=x['scenario_id'], agency=x['expected_agency'],
                     emergency_action='CONTINUE', created_date='2026-10-12T12:00:00+00:00') for x in self.demo]
        result = prioritize_from_c10(rows, NOW)
        self.assertEqual(len(result), 34)
        self.assertTrue(all(x['sla_status'] == 'SLA_UNKNOWN' for x in result))

    def test_verified_enrichment_exact(self):
        match = next(r for r in self.review if lookup({
            'request_type_code':r['request_type_code'], 'official_agency':r['official_agency'],
            'official_problem':r['official_problem'], 'official_problem_details':r['official_problem_details'],
            'official_additional_details':r['additional_details']}, self.official, self.review)['sla_status']=='MATCHED_EXACT_DETAIL')
        row = self.base(match['request_type_code'])
        row['verified_official_category'] = {
            'verified':True, 'source':'reviewed mapping fixture',
            'official_agency':match['official_agency'], 'official_problem':match['official_problem'],
            'official_problem_details':match['official_problem_details'],
            'official_additional_details':match['additional_details']}
        got = prioritize_from_c10([row], NOW)[0]
        self.assertEqual(got['sla_category_status'], 'VERIFIED')
        self.assertEqual(got['sla_status'], 'MATCHED_EXACT_DETAIL')
        self.assertGreater(got['sla_target_hours'], 0)

    def test_unverified_category_not_applied(self):
        row = self.base('RT020')
        row['verified_official_category'] = {'verified':False, 'source':'',
            'official_agency':'X', 'official_problem':'Y', 'official_problem_details':'Z'}
        got = prioritize_from_c10([row], NOW)[0]
        self.assertEqual(got['sla_category_status'], 'UNVERIFIED')
        self.assertEqual(got['sla_status'], 'SLA_UNKNOWN')

    def test_multiple_variants_same_code_not_guessed(self):
        code = 'RT020'
        variants = {(r['official_problem'], r['official_problem_details']) for r in self.review if r['request_type_code']==code}
        self.assertGreater(len(variants), 1)
        got = prioritize_from_c10([self.base(code)], NOW)[0]
        self.assertEqual(got['sla_status'], 'SLA_UNKNOWN')

if __name__ == '__main__':
    unittest.main(verbosity=2)
